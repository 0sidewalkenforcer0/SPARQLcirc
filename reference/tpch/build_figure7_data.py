#!/usr/bin/env python3
"""Join measured RDF and ProvSQL summaries into the TPC-H Figure 7 table."""

from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path
import sys
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

HERE = Path(__file__).resolve().parent
REFERENCE = HERE.parent
if str(REFERENCE) not in sys.path:
    sys.path.insert(0, str(REFERENCE))

from tpch.workload import PAPER_SCALE_FACTORS


TEMPLATES = (
    "Q01", "Q03", "Q04", "Q05", "Q06", "Q07",
    "Q08", "Q10", "Q12", "Q14", "Q15", "Q19",
)
RDF_METHODS = {
    "C-flat": "SPARQLcirc (flat)",
    "C-factorised": "SPARQLcirc (factored)",
}
ENGINE_LABELS = {
    "graphdb": "GraphDB 10.7.6",
    "graphdb-10.7.6": "GraphDB 10.7.6",
    "GraphDB 10.7.6": "GraphDB 10.7.6",
    "oxigraph": "Oxigraph 0.5.9",
    "oxigraph-0.5.9": "Oxigraph 0.5.9",
    "Oxigraph 0.5.9": "Oxigraph 0.5.9",
}
OUTPUT_FIELDS = (
    "template", "scale_factor", "engine", "mode", "status",
    "runtime_ms", "timeout_s", "data_kind",
)


class FigureDataError(RuntimeError):
    """Measured summaries cannot form the fixed Figure 7 matrix."""


def _read(path: Path) -> List[Dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _truth(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes")


def _runtime(row: Mapping[str, str], field: str) -> str:
    raw = row.get(field, "").strip()
    if not raw:
        return ""
    value = float(raw)
    if not math.isfinite(value) or value <= 0:
        raise FigureDataError("invalid %s value: %r" % (field, raw))
    return format(value, ".12g")


def _status(row: Mapping[str, str]) -> str:
    if _truth(row.get("complete", "")):
        return "ok"
    if int(row.get("timeout_instances") or 0) > 0:
        return "timeout"
    if int(row.get("failed_instances") or 0) > 0:
        return "failed"
    return "missing"


def _key(row: Mapping[str, str], engine: str, mode: str) -> Tuple[str, str, str, str]:
    return (row["template"], row["scale_factor"], engine, mode)


def build_rows(rdf_rows: Iterable[Mapping[str, str]],
               provsql_rows: Iterable[Mapping[str, str]],
               timeout_s: float = 3000.0) -> List[Dict[str, str]]:
    rows: Dict[Tuple[str, str, str, str], Dict[str, str]] = {}
    scales = set(PAPER_SCALE_FACTORS)
    templates = set(TEMPLATES)

    for source in rdf_rows:
        if source.get("template") not in templates or source.get("scale_factor") not in scales:
            continue
        method = source.get("method", "")
        if method not in RDF_METHODS:
            continue
        raw_engine = source.get("engine", "")
        if raw_engine not in ENGINE_LABELS:
            continue
        engine = ENGINE_LABELS[raw_engine]
        mode = RDF_METHODS[method]
        status = _status(source)
        value = _runtime(source, "median_component_method_e2e_ms") if status == "ok" else ""
        key = _key(source, engine, mode)
        if key in rows:
            raise FigureDataError("duplicate RDF summary row: %s" % (key,))
        rows[key] = {
            "template": source["template"],
            "scale_factor": source["scale_factor"],
            "engine": engine,
            "mode": mode,
            "status": status,
            "runtime_ms": value,
            "timeout_s": format(timeout_s, "g"),
            "data_kind": "measured",
        }

    for source in provsql_rows:
        if source.get("template") not in templates or source.get("scale_factor") not in scales:
            continue
        if source.get("method") != "ProvSQL":
            continue
        engine = "PostgreSQL 18.4"
        mode = "ProvSQL"
        status = _status(source)
        value = _runtime(source, "median_primary_total_ms") if status == "ok" else ""
        key = _key(source, engine, mode)
        if key in rows:
            raise FigureDataError("duplicate ProvSQL summary row: %s" % (key,))
        rows[key] = {
            "template": source["template"],
            "scale_factor": source["scale_factor"],
            "engine": engine,
            "mode": mode,
            "status": status,
            "runtime_ms": value,
            "timeout_s": format(timeout_s, "g"),
            "data_kind": "measured",
        }

    expected = {
        (template, scale, engine, mode)
        for template in TEMPLATES
        for scale in PAPER_SCALE_FACTORS
        for engine, mode in (
            ("GraphDB 10.7.6", "SPARQLcirc (flat)"),
            ("GraphDB 10.7.6", "SPARQLcirc (factored)"),
            ("Oxigraph 0.5.9", "SPARQLcirc (flat)"),
            ("Oxigraph 0.5.9", "SPARQLcirc (factored)"),
            ("PostgreSQL 18.4", "ProvSQL"),
        )
    }
    missing = sorted(expected.difference(rows))
    if missing:
        examples = ", ".join("/".join(item) for item in missing[:5])
        raise FigureDataError(
            "measured summaries are incomplete (%d rows missing; first: %s)"
            % (len(missing), examples)
        )

    order = {template: index for index, template in enumerate(TEMPLATES)}
    scale_order = {scale: index for index, scale in enumerate(PAPER_SCALE_FACTORS)}
    series_order = {
        ("GraphDB 10.7.6", "SPARQLcirc (flat)"): 0,
        ("GraphDB 10.7.6", "SPARQLcirc (factored)"): 1,
        ("Oxigraph 0.5.9", "SPARQLcirc (flat)"): 2,
        ("Oxigraph 0.5.9", "SPARQLcirc (factored)"): 3,
        ("PostgreSQL 18.4", "ProvSQL"): 4,
    }
    return sorted(rows.values(), key=lambda row: (
        order[row["template"]],
        series_order[(row["engine"], row["mode"])],
        scale_order[row["scale_factor"]],
    ))


def write_rows(path: Path, rows: Sequence[Mapping[str, str]]) -> None:
    if path.exists():
        raise FigureDataError("refusing to overwrite %s" % path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rdf-summary", required=True, type=Path)
    parser.add_argument("--provsql-summary", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--timeout", type=float, default=3000.0)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        rows = build_rows(_read(args.rdf_summary), _read(args.provsql_summary), args.timeout)
        write_rows(args.out, rows)
    except (OSError, ValueError, FigureDataError) as error:
        raise SystemExit("TPC-H Figure 7 data: %s" % error)
    print("wrote %d measured rows to %s" % (len(rows), args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
