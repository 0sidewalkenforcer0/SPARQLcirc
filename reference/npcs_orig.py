#!/usr/bin/env python3
"""Adapter for the original NPCS rewriter (Asma et al., WWW'24).

The original NPCS rewriter is distributed only as a binary fat jar,
``ReifySparqlByte.jar`` (repository https://github.com/ZubariaForthAcc/NPCS,
MIT licence; the copy checked on 2026-10-16 has SHA-256
328916c1c20294ddf492b6146bc0e8e542b2f7bc73abcb6d551ef64de6f17dce).  Its CLI is

    java -jar ReifySparqlByte.jar <scheme> path <query-file>
    java -jar ReifySparqlByte.jar <scheme> query "<query text>"

and it prints the rewritten query as ``Byte Array: [83, 69, ...]``: the signed
bytes of the UTF-8 text.  Its ``SPARQL_Star`` scheme emits only the occurrence
lookup ``<< s p o >> <http://example.org/occurrenceOf> ?t`` for every triple
pattern; it never matches the asserted triple.  The comparable layout of our
re-implementation is therefore ``SPARQL_Star_Pure``.

The original jar produces the provenance defined in the NPCS paper only for a
plain SELECT over one basic graph pattern (checked 2026-10-16 on Oxigraph and
GraphDB):

* OPTIONAL: the ``BIND(CONCAT(..., ?rightunion0, ..., ?rightoptional1, ...))``
  is placed after the UNION, so every answer's provenance is unbound;
* UNION: one branch is not reified and no provenance column is projected;
* MINUS: it emits an OPTIONAL, which changes the answer set;
* DISTINCT and FILTER: no provenance column is projected.

``static_gate`` therefore accepts only a plain SELECT with an explicit list of
projected variables over one non-empty BGP whose terms are IRIs, literals, or
variables.  Everything else is refused before the jar or an endpoint is
contacted.  ``check_rewritten`` adds the output-side checks (exactly one
provenance projection; no unbound-provenance construction).  The result of a
refusal is a reason string that the experiment runners record verbatim.

No checksum or digest is computed here; the jar identity is checked outside
the result tree (see reference/www27/RUNBOOK.md).
"""
from __future__ import annotations

from dataclasses import dataclass, field
import importlib.util
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple


ORIG_JAR_ENV = "NPCS_ORIG_JAR"
PROVENANCE_VARIABLE = "finalprovennacevariable"
# The original jar's scheme names.  The lookup-only layouts of our rewriter
# carry the ``_Pure`` suffix; the original has no asserted-triple layout.
ORIG_SCHEME_FOR_LAYOUT = {
    "SPARQL_Star_Pure": "SPARQL_Star",
    "Standard_Pure": "Standard",
}
UNSUPPORTED_PREFIX = (
    "original NPCS jar refused: it yields the provenance of the NPCS paper only for a "
    "plain SELECT over one basic graph pattern (checked 2026-10-16)"
)
_BYTE_ARRAY = re.compile(r"Byte Array:\s*\[([-0-9,\s]*)\]")
_PROVENANCE_PROJECTION = re.compile(
    r"\bAS\s+\?" + PROVENANCE_VARIABLE + r"\b", re.IGNORECASE
)
_ALGEBRA_FEATURES = {
    "LeftJoin": "OPTIONAL",
    "Union": "UNION",
    "Minus": "MINUS",
    "Filter": "FILTER",
    "Extend": "BIND or projection expression",
    "Distinct": "DISTINCT",
    "Reduced": "REDUCED",
    "Slice": "LIMIT/OFFSET",
    "OrderBy": "ORDER BY",
    "Group": "GROUP BY",
    "AggregateJoin": "aggregate",
    "ToMultiSet": "VALUES or subquery",
    "values": "VALUES",
    "Graph": "GRAPH",
    "Service": "SERVICE",
    "Join": "nested group join",
    "SelectQuery": "subquery",
}
_PARSE_FEATURES = {
    "modifier": "DISTINCT/REDUCED",
    "datasetClause": "FROM/FROM NAMED",
    "groupby": "GROUP BY",
    "having": "HAVING",
    "orderby": "ORDER BY",
    "limitoffset": "LIMIT/OFFSET",
    "valuesClause": "VALUES",
}


class OrigNpcsError(RuntimeError):
    """The original rewriter failed or produced an unusable query."""

    def __init__(self, status: str, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass
class GateResult:
    supported: bool
    reason: Optional[str]
    features: List[str] = field(default_factory=list)

    def document(self) -> Dict[str, Any]:
        return {
            "supported": self.supported,
            "reason": self.reason,
            "features": list(self.features),
            "rule": "plain SELECT ?v... over one BGP of IRIs, literals and variables",
        }


def orig_scheme(scheme: str) -> str:
    """Map a lookup-only layout of our rewriter to the original's scheme name."""
    try:
        return ORIG_SCHEME_FOR_LAYOUT[scheme]
    except KeyError as exc:
        raise ValueError(
            "the original NPCS jar emits only the lookup-only layout; use %s, not %r"
            % (" or ".join(sorted(ORIG_SCHEME_FOR_LAYOUT)), scheme)
        ) from exc


def _algebra_features(node: Any, found: List[str], top: bool = True) -> None:
    name = getattr(node, "name", None)
    if name is None:
        return
    if name in _ALGEBRA_FEATURES and not (top and name == "SelectQuery"):
        feature = _ALGEBRA_FEATURES[name]
        if feature not in found:
            found.append(feature)
    for key in node.keys():
        value = node[key]
        if hasattr(value, "name"):
            _algebra_features(value, found, False)
        elif isinstance(value, (list, tuple)):
            for item in value:
                if hasattr(item, "name"):
                    _algebra_features(item, found, False)


def static_gate(query_text: str) -> GateResult:
    """Decide from the query alone whether the original jar may rewrite it."""
    try:
        from rdflib.plugins.sparql.parser import parseQuery
        from rdflib.plugins.sparql.algebra import translateQuery
        from rdflib.paths import Path as RdflibPath
        from rdflib.term import BNode, Literal, URIRef, Variable
    except ImportError as exc:  # pragma: no cover - environment guard
        raise RuntimeError("the N-orig query gate needs rdflib") from exc
    try:
        parsed = parseQuery(query_text)
        query = parsed[1]
        algebra = translateQuery(parsed).algebra
    except Exception as exc:  # rdflib raises several parser exception types
        return GateResult(False, "%s; the query does not parse: %s" % (UNSUPPORTED_PREFIX, exc))

    features: List[str] = []
    if getattr(query, "name", None) != "SelectQuery":
        features.append(str(getattr(query, "name", "non-SELECT query")))
    keys = set(query.keys())
    for key, feature in _PARSE_FEATURES.items():
        if key == "modifier" and key in keys:
            feature = str(query["modifier"]).upper()
        if key in keys and feature not in features:
            features.append(feature)
    if "projection" not in keys:
        features.append("SELECT *")
    else:
        for item in query["projection"]:
            if "expr" in item.keys() or "var" not in item.keys():
                if "projection expression" not in features:
                    features.append("projection expression")
    body = algebra.get("p") if hasattr(algebra, "get") else None
    if getattr(body, "name", None) != "Project":
        _algebra_features(body, features)
    else:
        pattern = body.get("p")
        if getattr(pattern, "name", None) != "BGP":
            _algebra_features(pattern, features)
        else:
            triples = list(pattern.get("triples") or ())
            if not triples:
                features.append("empty graph pattern")
            for triple in triples:
                for position, term in zip(("subject", "predicate", "object"), triple):
                    if isinstance(term, RdflibPath):
                        if "property path" not in features:
                            features.append("property path")
                    elif isinstance(term, BNode):
                        if "blank node in a triple pattern" not in features:
                            features.append("blank node in a triple pattern")
                    elif not isinstance(term, (Variable, URIRef, Literal)):
                        label = "unsupported %s term %s" % (position, type(term).__name__)
                        if label not in features:
                            features.append(label)
    if features:
        return GateResult(
            False,
            "%s; this query uses: %s" % (UNSUPPORTED_PREFIX, ", ".join(features)),
            features,
        )
    return GateResult(True, None, [])


def decode_byte_array(stdout: str) -> str:
    """Decode the ``Byte Array: [...]`` line printed by the original jar."""
    matches = _BYTE_ARRAY.findall(stdout)
    if len(matches) != 1:
        raise OrigNpcsError(
            "rewrite-error",
            "original NPCS jar printed %d byte arrays; expected exactly one" % len(matches),
        )
    values = [item.strip() for item in matches[0].split(",") if item.strip()]
    try:
        raw = bytes(int(item) & 0xFF for item in values)
    except ValueError as exc:
        raise OrigNpcsError("rewrite-error", "malformed byte array: %s" % exc) from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OrigNpcsError("rewrite-error", "byte array is not UTF-8: %s" % exc) from exc
    if not text.strip():
        raise OrigNpcsError("rewrite-error", "original NPCS jar returned an empty query")
    return text


def check_rewritten(text: str) -> Optional[str]:
    """Return a refusal reason when the decoded query cannot carry NPCS provenance."""
    projections = _PROVENANCE_PROJECTION.findall(text)
    if len(projections) != 1:
        return (
            "%s; the rewritten query projects ?%s %d times (expected once), so it "
            "would return no provenance" % (UNSUPPORTED_PREFIX, PROVENANCE_VARIABLE, len(projections))
        )
    # The defect observed for OPTIONAL: a BIND over two union-branch variables
    # after the UNION, which leaves the provenance unbound on every row.
    if re.search(r"\?rightunion\d+\s*,\s*\"[^\"]*\"\s*,\s*\?rightoptional\d+", text):
        return (
            "%s; the rewritten query combines ?rightunionN and ?rightoptionalN after a "
            "UNION, which leaves the provenance unbound" % UNSUPPORTED_PREFIX
        )
    return None


def run_original(
    jar: Path,
    scheme: str,
    query_path: Path,
    java: str = "java",
    timeout_s: float = 120.0,
) -> Tuple[str, Dict[str, Any]]:
    """Run the original jar once in a fresh JVM and decode its output."""
    command = [str(java), "-jar", str(jar), str(scheme), "path", str(query_path)]
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command, capture_output=True, timeout=timeout_s, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise OrigNpcsError(
            "timeout", "original NPCS rewrite exceeded %.6gs" % timeout_s
        ) from exc
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    stdout = completed.stdout.decode("utf-8", errors="replace")
    stderr = completed.stderr.decode("utf-8", errors="replace")
    if completed.returncode != 0:
        raise OrigNpcsError(
            "rewrite-error",
            "original NPCS jar exited %d: %s" % (completed.returncode, stderr.strip()[-1000:]),
        )
    text = decode_byte_array(stdout)
    return text, {
        "kind": "npcs-original-jar-rewrite",
        "orig_scheme": scheme,
        "rewrite_ms": round(elapsed_ms, 6),
        "rewritten_query_bytes": len(text.encode("utf-8")),
        "stdout_bytes": len(completed.stdout),
        "stderr_bytes": len(completed.stderr),
    }


def parse_check(jar: Path, query_path: Path, java: str = "java",
                timeout_s: float = 120.0) -> Optional[str]:
    """Parse a query with RDF4J's SPARQL parser through our jar; return an error or None."""
    command = [str(java), "-jar", str(jar), "parsecheck", "path", str(query_path)]
    try:
        completed = subprocess.run(
            command, capture_output=True, timeout=timeout_s, check=False
        )
    except subprocess.TimeoutExpired:
        return "RDF4J parse check timed out"
    output = completed.stdout.decode("utf-8", errors="replace").strip()
    if completed.returncode != 0 or output != "PARSE_OK":
        detail = output or completed.stderr.decode("utf-8", errors="replace").strip()
        return "RDF4J does not parse the rewritten query: %s" % detail[-1000:]
    return None


def _diff_harness() -> Any:
    path = Path(__file__).resolve().parents[1] / "engine" / "verify" / "diff_harness.py"
    spec = importlib.util.spec_from_file_location("npcs_diff_harness", path)
    if spec is None or spec.loader is None:  # pragma: no cover - repository layout guard
        raise RuntimeError("cannot load %s" % path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def equivalent_to_reimplementation(original: str, ours: str) -> bool:
    """Compare up to gensym renaming and commutative ⊗/⊕ argument order.

    This is the normalisation of engine/verify/diff_harness.py, which found the
    re-implementation equal to the original on 139/139 WatDiv BGP queries.
    """
    harness = _diff_harness()
    return harness.norm(original) == harness.norm(ours)


def default_jar() -> Optional[str]:
    value = os.environ.get(ORIG_JAR_ENV, "").strip()
    return value or None


def _main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Gate and run the original NPCS jar on one query.")
    parser.add_argument("query", type=Path)
    parser.add_argument("--orig-jar", default=default_jar())
    parser.add_argument("--scheme", default="SPARQL_Star_Pure")
    parser.add_argument("--java", default="java")
    args = parser.parse_args(argv)
    text = args.query.read_text(encoding="utf-8")
    gate = static_gate(text)
    if not gate.supported:
        print(json.dumps({"status": "unsupported", "gate": gate.document()}, ensure_ascii=False))
        return 3
    if not args.orig_jar:
        parser.error("set --orig-jar or %s" % ORIG_JAR_ENV)
    rewritten, metrics = run_original(Path(args.orig_jar), orig_scheme(args.scheme), args.query, args.java)
    problem = check_rewritten(rewritten)
    if problem:
        print(json.dumps({"status": "unsupported", "reason": problem}, ensure_ascii=False))
        return 3
    print(rewritten, end="" if rewritten.endswith("\n") else "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
