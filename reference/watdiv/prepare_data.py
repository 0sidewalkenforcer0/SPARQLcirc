#!/usr/bin/env python3
"""Generate a deduplicated WatDiv base graph and RDF-star 1.1 mixed layout.

The command runs the external WatDiv 0.6 generator, applies RDF set semantics
by sorting and removing exact duplicate N-Triples lines, and then writes the
asserted-plus-occurrence layout consumed by the evaluation. It records counts
and byte sizes, but deliberately does not compute file digests.
"""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Dict, Optional, Sequence, Tuple


HERE = Path(__file__).resolve().parent
REFERENCE = HERE.parent
if str(REFERENCE) not in sys.path:
    sys.path.insert(0, str(REFERENCE))

from watdiv import reify


SCHEMA = "watdiv-rdf-star11-data-v1"
RDF_STAR_PROFILE = "RDF-star 1.1 quoted triple plus occurrenceOf"
DUPLICATE_POLICY = "sort unique exact N-Triples lines under the C locale"
NTRIPLES_LINE = re.compile(
    r"^[ \t]*<[^>\r\n]+>[ \t]+<[^>\r\n]+>[ \t]+.+[ \t]+\.[ \t]*$"
)


class PreparationError(RuntimeError):
    """A WatDiv dataset could not be prepared without ambiguity."""


def _write_text(path: Path, value: str) -> None:
    if path.exists():
        raise PreparationError("refusing to overwrite %s" % path)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())


def _write_json(path: Path, value: Any) -> None:
    _write_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _copy_file(source: Path, target: Path) -> None:
    if target.exists():
        raise PreparationError("refusing to overwrite %s" % target)
    with source.open("rb") as input_handle, target.open("xb") as output_handle:
        shutil.copyfileobj(input_handle, output_handle)
        output_handle.flush()
        os.fsync(output_handle.fileno())


def _validate_scale(scale: str) -> None:
    if re.fullmatch(r"[1-9][0-9]*", scale) is None:
        raise PreparationError(
            "WatDiv 0.6 data scale must be a positive integer: %s" % scale
        )


def _validate_ntriples(path: Path) -> int:
    count = 0
    with path.open(encoding="utf-8", newline="") as source:
        for line_number, line in enumerate(source, 1):
            if "\r" in line:
                raise PreparationError(
                    "carriage return in WatDiv N-Triples line %d" % line_number
                )
            stripped = line.rstrip("\n")
            if not stripped or stripped.lstrip().startswith("#"):
                raise PreparationError(
                    "blank or comment WatDiv N-Triples line %d" % line_number
                )
            if NTRIPLES_LINE.fullmatch(stripped) is None:
                raise PreparationError(
                    "invalid WatDiv N-Triples line %d" % line_number
                )
            count += 1
    if count == 0:
        raise PreparationError("WatDiv generator produced no triples")
    return count


def _run_generator(
    watdiv: Path,
    model: Path,
    scale: str,
    working_directory: Path,
    raw_path: Path,
) -> Dict[str, Any]:
    stderr_path = working_directory / "generator.stderr"
    environment = dict(os.environ)
    environment["WATDIV_RUN_DIR"] = str(working_directory.resolve())
    command = [str(watdiv.resolve()), "-d", str(model.resolve()), scale]
    with raw_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
        completed = subprocess.run(
            command,
            cwd=str(working_directory.resolve()),
            env=environment,
            stdout=stdout,
            stderr=stderr,
            check=False,
        )
    if completed.returncode != 0:
        tail = stderr_path.read_text(encoding="utf-8", errors="replace")[-1000:].strip()
        raise PreparationError(
            "WatDiv generator failed with exit code %d: %s"
            % (completed.returncode, tail)
        )
    return {
        "argv": [watdiv.name, "-d", model.name, scale],
        "exit_code": completed.returncode,
        "stderr": "generator.stderr",
    }


def _deduplicate(
    raw_path: Path,
    base_path: Path,
    sort_command: str,
    sort_temporary_directory: Optional[Path],
) -> Dict[str, Any]:
    resolved_sort = shutil.which(sort_command)
    if resolved_sort is None:
        raise PreparationError("sort executable is unavailable: %s" % sort_command)
    environment = dict(os.environ)
    environment["LC_ALL"] = "C"
    if sort_temporary_directory is not None:
        temporary = sort_temporary_directory.resolve()
        if not temporary.is_dir():
            raise PreparationError("sort temporary directory does not exist: %s" % temporary)
        environment["TMPDIR"] = str(temporary)
    stderr_path = base_path.parent / "sort.stderr"
    with base_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
        completed = subprocess.run(
            [resolved_sort, "-u", "--", str(raw_path.resolve())],
            env=environment,
            stdout=stdout,
            stderr=stderr,
            check=False,
        )
    if completed.returncode != 0:
        tail = stderr_path.read_text(encoding="utf-8", errors="replace")[-1000:].strip()
        raise PreparationError(
            "exact-line deduplication failed with exit code %d: %s"
            % (completed.returncode, tail)
        )
    return {
        "command": [Path(resolved_sort).name, "-u", "--", "generator.raw.nt"],
        "stderr": "sort.stderr",
        "locale": "C",
    }


def prepare(
    output: Path,
    scale: str,
    watdiv_version: str,
    watdiv: Path,
    model: Path,
    sort_command: str = "sort",
    sort_temporary_directory: Optional[Path] = None,
    keep_generator_output: bool = False,
) -> Dict[str, Any]:
    """Create one new scale directory and return its metadata."""
    _validate_scale(scale)
    final = output.resolve()
    partial = final.with_name(final.name + ".partial")
    if final.exists() or partial.exists():
        raise PreparationError("refusing to reuse output or partial directory: %s" % final)
    if not watdiv.is_file():
        raise PreparationError("WatDiv executable does not exist: %s" % watdiv)
    if not model.is_file() or model.stat().st_size == 0:
        raise PreparationError("WatDiv model does not exist or is empty: %s" % model)

    partial.mkdir(parents=True)
    model_copy = partial / "model.txt"
    _copy_file(model, model_copy)
    raw_path = partial / "generator.raw.nt"
    base_path = partial / "base.nt"
    mixed_path = partial / "mixed-rdfstar11.ttls"

    generator_record = _run_generator(watdiv, model_copy, scale, partial, raw_path)
    raw_count = _validate_ntriples(raw_path)
    raw_bytes = raw_path.stat().st_size
    sort_record = _deduplicate(
        raw_path, base_path, sort_command, sort_temporary_directory
    )
    unique_count = _validate_ntriples(base_path)
    if unique_count > raw_count:
        raise PreparationError(
            "deduplication increased the statement count: %d > %d"
            % (unique_count, raw_count)
        )

    logical_count, mixed_count = reify.reify_file(
        base_path, mixed_path, scheme=reify.RDF_STAR, pure=False
    )
    if logical_count != unique_count or mixed_count != unique_count * 2:
        raise PreparationError("mixed layout count differs from the deduplicated base graph")

    state_path = partial / "saved.txt"
    if not state_path.is_file() or state_path.stat().st_size == 0:
        raise PreparationError("WatDiv generator did not create a reusable saved.txt state")

    if not keep_generator_output:
        raw_path.unlink()

    metadata = {
        "schema": SCHEMA,
        "watdiv_version": watdiv_version,
        "scale_factor": scale,
        "model_file": "model.txt",
        "model_source_name": model.name,
        "model_bytes": model_copy.stat().st_size,
        "generator_seed_control": "unavailable in the upstream WatDiv 0.6 CLI",
        "rerun_semantics": "a new random WatDiv sample; preserve this output for exact reuse",
        "duplicate_policy": DUPLICATE_POLICY,
        "token_order": "base.nt line order after C-locale exact-line sorting",
        "rdf_star_profile": RDF_STAR_PROFILE,
        "rdf_star_12_permitted": False,
        "generator": {
            **generator_record,
            "raw_statement_count": raw_count,
            "raw_bytes": raw_bytes,
            "raw_output_retained": keep_generator_output,
            "raw_output": "generator.raw.nt" if keep_generator_output else None,
            "query_state": "saved.txt",
        },
        "deduplication": {
            **sort_record,
            "unique_statement_count": unique_count,
            "duplicates_removed": raw_count - unique_count,
        },
        "layouts": {
            "base": {
                "path": "base.nt",
                "statement_count": unique_count,
                "bytes": base_path.stat().st_size,
            },
            "mixed": {
                "path": "mixed-rdfstar11.ttls",
                "asserted_statement_count": unique_count,
                "occurrence_statement_count": unique_count,
                "physical_statement_count": mixed_count,
                "bytes": mixed_path.stat().st_size,
            },
        },
    }
    _write_json(partial / "dataset.json", metadata)
    audit(partial / "dataset.json", scan_mixed=True)
    os.replace(partial, final)
    return metadata


def _triple_terms(line: str, line_number: int) -> Tuple[str, str, str]:
    stripped = line.strip()
    parts = stripped[:-1].strip().split(None, 2) if stripped.endswith(".") else []
    if len(parts) != 3:
        raise PreparationError("invalid base N-Triples line %d" % line_number)
    return parts[0], parts[1], parts[2]


def audit(metadata_path: Path, scan_mixed: bool = True) -> Dict[str, Any]:
    """Validate metadata and, by default, every base/mixed statement pair."""
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata.get("schema") != SCHEMA:
        raise PreparationError("unsupported WatDiv dataset metadata schema")
    if metadata.get("rdf_star_profile") != RDF_STAR_PROFILE:
        raise PreparationError("dataset does not declare the RDF-star 1.1 profile")
    if metadata.get("rdf_star_12_permitted") is not False:
        raise PreparationError("dataset does not prohibit RDF 1.2 reification")
    if metadata.get("duplicate_policy") != DUPLICATE_POLICY:
        raise PreparationError("dataset does not declare the exact-line deduplication policy")
    if metadata.get("generator_seed_control") != (
        "unavailable in the upstream WatDiv 0.6 CLI"
    ):
        raise PreparationError("dataset does not record the WatDiv seed-control boundary")

    root = metadata_path.parent
    base_path = root / metadata["layouts"]["base"]["path"]
    mixed_path = root / metadata["layouts"]["mixed"]["path"]
    state_path = root / metadata["generator"]["query_state"]
    model_path = root / metadata["model_file"]
    for path in (base_path, mixed_path, state_path, model_path):
        if not path.is_file() or path.stat().st_size == 0:
            raise PreparationError("dataset artifact is missing or empty: %s" % path)
    if model_path.stat().st_size != int(metadata["model_bytes"]):
        raise PreparationError("model size differs from dataset metadata")
    if base_path.stat().st_size != int(metadata["layouts"]["base"]["bytes"]):
        raise PreparationError("base layout size differs from dataset metadata")
    if mixed_path.stat().st_size != int(metadata["layouts"]["mixed"]["bytes"]):
        raise PreparationError("mixed layout size differs from dataset metadata")

    expected = int(metadata["layouts"]["base"]["statement_count"])
    mixed_metadata = metadata["layouts"]["mixed"]
    if (
        int(mixed_metadata["asserted_statement_count"]) != expected
        or int(mixed_metadata["occurrence_statement_count"]) != expected
        or int(mixed_metadata["physical_statement_count"]) != expected * 2
        or int(metadata["deduplication"]["unique_statement_count"]) != expected
    ):
        raise PreparationError("base, mixed, and deduplication counts disagree")
    raw_count = int(metadata["generator"]["raw_statement_count"])
    duplicates = int(metadata["deduplication"]["duplicates_removed"])
    if raw_count - expected != duplicates or duplicates < 0:
        raise PreparationError("raw, unique, and duplicate counts disagree")
    if metadata["generator"]["raw_output_retained"]:
        raw_path = root / metadata["generator"]["raw_output"]
        if not raw_path.is_file() or raw_path.stat().st_size != int(
            metadata["generator"]["raw_bytes"]
        ):
            raise PreparationError("retained generator output differs from metadata")
    scanned = None
    if scan_mixed:
        scanned = 0
        with base_path.open(encoding="utf-8") as base, mixed_path.open(
            encoding="utf-8"
        ) as mixed:
            for index, base_line in enumerate(base):
                subject, predicate, obj = _triple_terms(base_line, index + 1)
                asserted = mixed.readline()
                occurrence = mixed.readline()
                expected_asserted = "%s %s %s .\n" % (subject, predicate, obj)
                expected_occurrence = (
                    "<< %s %s %s >> <%s> <urn:t:%d> .\n"
                    % (subject, predicate, obj, reify.OCC, index)
                )
                if asserted != expected_asserted:
                    raise PreparationError(
                        "mixed asserted statement differs at base line %d" % (index + 1)
                    )
                if (
                    "rdf:reifies" in occurrence
                    or "rdf-syntax-ns#reifies" in occurrence
                    or "<<(" in occurrence
                ):
                    raise PreparationError(
                        "RDF 1.2 reification found at base line %d" % (index + 1)
                    )
                if occurrence != expected_occurrence:
                    raise PreparationError(
                        "mixed occurrence statement differs at base line %d" % (index + 1)
                    )
                scanned += 1
            if mixed.readline():
                raise PreparationError("mixed layout contains trailing statements")
        if scanned != expected:
            raise PreparationError(
                "base layout has %d statements; metadata declares %d" % (scanned, expected)
            )
    return {
        "status": "ok",
        "scale_factor": metadata["scale_factor"],
        "statement_count": expected,
        "duplicates_removed": duplicates,
        "mixed_content_scanned": scan_mixed,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)
    generate = subparsers.add_parser(
        "generate", help="run WatDiv and create deduplicated base and mixed layouts"
    )
    generate.add_argument("--watdiv", required=True, type=Path)
    generate.add_argument("--model", required=True, type=Path)
    generate.add_argument("--scale", required=True)
    generate.add_argument("--out", required=True, type=Path)
    generate.add_argument("--watdiv-version", default="0.6")
    generate.add_argument("--sort", default="sort", dest="sort_command")
    generate.add_argument("--sort-tmp", type=Path)
    generate.add_argument("--keep-generator-output", action="store_true")
    check = subparsers.add_parser("audit", help="audit an existing prepared scale")
    check.add_argument("metadata", type=Path)
    check.add_argument(
        "--quick", action="store_true", help="check metadata and sizes without scanning content"
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "generate":
            result = prepare(
                output=args.out,
                scale=args.scale,
                watdiv_version=args.watdiv_version,
                watdiv=args.watdiv,
                model=args.model,
                sort_command=args.sort_command,
                sort_temporary_directory=args.sort_tmp,
                keep_generator_output=args.keep_generator_output,
            )
            summary = {
                "status": "ok",
                "scale_factor": result["scale_factor"],
                "statements": result["layouts"]["base"]["statement_count"],
                "duplicates_removed": result["deduplication"]["duplicates_removed"],
            }
        else:
            summary = audit(args.metadata.resolve(), scan_mixed=not args.quick)
    except (OSError, ValueError, PreparationError) as error:
        parser.exit(1, "watdiv data: error: %s\n" % error)
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
