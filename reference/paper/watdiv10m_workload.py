#!/usr/bin/env python3
"""Create and audit the WatDiv 10M evaluation workload.

The workload occupies a versioned track alongside the historical
``reference/paper/queries/watdiv`` files and paper manifests, which remain
unchanged. File identity is checked by complete byte equality where needed;
the module does not compute checksums or cryptographic digests.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


OFFICIAL_TEMPLATES = tuple(
    ["L%d" % value for value in range(1, 6)]
    + ["S%d" % value for value in range(1, 8)]
    + ["F%d" % value for value in range(1, 6)]
    + ["C%d" % value for value in range(1, 4)]
)
OPTIONAL_TEMPLATES = tuple("O%d" % value for value in range(1, 6))
NON_PATH_TEMPLATES = OFFICIAL_TEMPLATES + OPTIONAL_TEMPLATES
BOUND_PATH_TEMPLATES = ("P-plus", "P-star", "P-alt")
ALL_PAIRS_PATH_TEMPLATE = "P-plus-all"
EXPECTED_NON_PATH = 250
EXPECTED_PATH = 31
EXPECTED_TOTAL = EXPECTED_NON_PATH + EXPECTED_PATH
QUERY_COLUMNS = (
    "query_id",
    "class",
    "template",
    "instance",
    "query_file",
    "query_bytes",
    "query_lines",
    "source_kind",
    "generator_version",
    "generator_seed",
    "selection_seed",
    "recurrence_factor",
    "template_file",
    "state_file",
    "source_iri",
)
SOURCE_COLUMNS = (
    "source_id",
    "iri",
    "stratum",
    "reachable_count",
    "max_hops",
    "selection_method",
    "selection_seed",
)
SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")
IRI = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:[^\s<>\"{}|^`\\]+$")
SELECT_START = re.compile(r"(?mi)^[ \t]*SELECT(?=[ \t\r\n])")
NO_EXPOSED_SEED = "not-exposed-by-watdiv-0.6-query-cli"


class WorkloadError(ValueError):
    """The workload could not be created or does not satisfy its contract."""


@dataclass(frozen=True)
class PathSource:
    source_id: str
    iri: str
    stratum: str
    reachable_count: int
    max_hops: int
    selection_method: str
    selection_seed: str


@dataclass(frozen=True)
class QueryRecord:
    query_id: str
    class_name: str
    template: str
    instance: str
    query_file: str
    query_bytes: str
    query_lines: str
    source_kind: str
    generator_version: str
    generator_seed: str
    selection_seed: str
    recurrence_factor: str
    template_file: str
    state_file: str
    source_iri: str

    def row(self) -> Dict[str, str]:
        value = asdict(self)
        value["class"] = value.pop("class_name")
        return value


GeneratorRunner = Callable[[Sequence[str], Path], Tuple[bytes, bytes]]


def split_generated_queries(text: str, expected: int) -> List[str]:
    """Split and preserve the plain sequence emitted by WatDiv 0.6 ``-q``."""
    matches = list(SELECT_START.finditer(text))
    if not matches:
        raise WorkloadError("WatDiv output contains no top-level SELECT query")
    if text[:matches[0].start()].strip():
        raise WorkloadError("unexpected text before the first generated SELECT query")
    queries = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        query = text[match.start():end].strip() + "\n"
        if "{" not in query or "}" not in query:
            raise WorkloadError("generated query %d has no complete graph pattern" % index)
        queries.append(query)
    if len(queries) != expected:
        raise WorkloadError(
            "WatDiv emitted %d queries; expected exactly %d" % (len(queries), expected)
        )
    return queries


def read_path_sources(path: Path) -> List[PathSource]:
    """Read ten preselected path sources and their selection evidence."""
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if tuple(reader.fieldnames or ()) != SOURCE_COLUMNS:
            raise WorkloadError(
                "path-source TSV header must be exactly: %s" % "\t".join(SOURCE_COLUMNS)
            )
        rows = list(reader)
    if len(rows) != 10:
        raise WorkloadError("path-source TSV must contain exactly 10 sources")
    sources: List[PathSource] = []
    for line_number, row in enumerate(rows, 2):
        if None in row or any(value is None for value in row.values()):
            raise WorkloadError("malformed path-source TSV row at line %d" % line_number)
        source_id = row["source_id"]
        iri = row["iri"]
        if not SAFE_ID.fullmatch(source_id):
            raise WorkloadError("unsafe source_id at line %d: %r" % (line_number, source_id))
        if not IRI.fullmatch(iri):
            raise WorkloadError("unsafe or non-absolute IRI at line %d: %r" % (line_number, iri))
        try:
            reachable_count = int(row["reachable_count"])
            max_hops = int(row["max_hops"])
        except ValueError as exc:
            raise WorkloadError("non-integer path statistic at line %d" % line_number) from exc
        if reachable_count < 0 or max_hops < 0:
            raise WorkloadError("negative path statistic at line %d" % line_number)
        if not row["stratum"] or not row["selection_method"] or not row["selection_seed"]:
            raise WorkloadError("incomplete selection evidence at line %d" % line_number)
        sources.append(PathSource(
            source_id,
            iri,
            row["stratum"],
            reachable_count,
            max_hops,
            row["selection_method"],
            row["selection_seed"],
        ))
    if len({source.source_id for source in sources}) != 10:
        raise WorkloadError("path source IDs are not unique")
    if len({source.iri for source in sources}) != 10:
        raise WorkloadError("path source IRIs are not unique")
    if len({source.selection_method for source in sources}) != 1:
        raise WorkloadError("all path sources must use one frozen selection method")
    if len({source.selection_seed for source in sources}) != 1:
        raise WorkloadError("all path sources must use one frozen selection seed")
    return sources


def _default_runner(command: Sequence[str], cwd: Path) -> Tuple[bytes, bytes]:
    completed = subprocess.run(
        list(command),
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise WorkloadError(
            "WatDiv query generation failed with exit %d: %s"
            % (completed.returncode, detail[:1000])
        )
    return completed.stdout, completed.stderr


def _copy_exact(source: Path, target: Path) -> None:
    if not source.is_file() or source.stat().st_size == 0:
        raise WorkloadError("required input is missing or empty: %s" % source)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    if source.read_bytes() != target.read_bytes():
        raise WorkloadError("exact input copy verification failed: %s" % source)


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(value)


def _write_json(path: Path, value: Any) -> None:
    _write_text(
        path,
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _write_query_list(path: Path, records: Iterable[QueryRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=QUERY_COLUMNS, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        for record in records:
            writer.writerow(record.row())


def _path_template_directory() -> Path:
    return Path(__file__).resolve().parent / "workload_templates" / "watdiv10m"


def _instantiate_path(template: str, source: Optional[PathSource]) -> str:
    marker = "__SOURCE_IRI__"
    if source is None:
        if marker in template:
            raise WorkloadError("all-pairs path template unexpectedly needs a source")
        return template.rstrip() + "\n"
    if template.count(marker) != 1:
        raise WorkloadError("bound path template must contain one source marker")
    return template.replace(marker, source.iri).rstrip() + "\n"


def _line_count(path: Path) -> int:
    with path.open("rb") as handle:
        return sum(1 for _line in handle)


def _assert_no_digest_fields(value: Any, context: str = "metadata") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            lowered = str(key).lower()
            if "checksum" in lowered or "digest" in lowered or lowered.endswith(
                ("_sha", "_sha1", "_sha256", "_sha512")
            ):
                raise WorkloadError("digest-bearing field in %s: %s" % (context, key))
            _assert_no_digest_fields(child, context)
    elif isinstance(value, list):
        for child in value:
            _assert_no_digest_fields(child, context)


def audit_workload(batch: Path) -> Dict[str, Any]:
    """Validate exact membership, query files, and no-digest metadata."""
    query_list = batch / "query-list.tsv"
    metadata_path = batch / "workload.json"
    if not query_list.is_file() or not metadata_path.is_file():
        raise WorkloadError("batch lacks query-list.tsv or workload.json")
    with query_list.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if tuple(reader.fieldnames or ()) != QUERY_COLUMNS:
            raise WorkloadError("query-list.tsv has an unexpected schema")
        rows = list(reader)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if not isinstance(metadata, dict) or metadata.get("schema") != "watdiv-10m-workload-v1":
        raise WorkloadError("workload.json has an unexpected schema")
    _assert_no_digest_fields(metadata)
    expected_metadata_counts = {
        "official_templates": 20,
        "optional_extension_templates": 5,
        "path_templates": 4,
        "non_path_queries": EXPECTED_NON_PATH,
        "path_queries": EXPECTED_PATH,
        "queries": EXPECTED_TOTAL,
    }
    if metadata.get("counts") != expected_metadata_counts:
        raise WorkloadError("workload.json counts do not match the formal workload")
    if metadata.get("old_repository_queries_modified") is not False:
        raise WorkloadError("workload metadata does not preserve the historical query track")
    generator = metadata.get("generator")
    if not isinstance(generator, dict) or generator.get("version") != "0.6":
        raise WorkloadError("workload metadata does not freeze WatDiv 0.6")
    if (
        generator.get("query_count_per_non_path_template") != 10
        or generator.get("recurrence_factor") != 1
        or generator.get("duplicate_policy")
        != "preserve-emitted-instances-in-order"
    ):
        raise WorkloadError("workload metadata does not describe the generation protocol")
    if not isinstance(metadata.get("dataset_id"), str) or not metadata["dataset_id"]:
        raise WorkloadError("workload metadata has no dataset identifier")
    if len(rows) != EXPECTED_TOTAL:
        raise WorkloadError("query list has %d rows; expected %d" % (len(rows), EXPECTED_TOTAL))
    expected_ids = {
        "%s-%02d" % (template, instance)
        for template in NON_PATH_TEMPLATES
        for instance in range(10)
    }
    expected_ids.update(
        "%s-%02d" % (template, instance)
        for template in BOUND_PATH_TEMPLATES
        for instance in range(10)
    )
    expected_ids.add(ALL_PAIRS_PATH_TEMPLATE + "-00")
    ids = [row["query_id"] for row in rows]
    if len(set(ids)) != len(ids) or set(ids) != expected_ids:
        raise WorkloadError("query IDs are duplicated, missing, or unexpected")
    generated_by_template: Dict[str, List[str]] = {}
    for template in NON_PATH_TEMPLATES:
        transcript = batch / "generation" / (template + ".generated.rq")
        if not transcript.is_file():
            raise WorkloadError("generation transcript is missing: %s" % transcript)
        generated_by_template[template] = split_generated_queries(
            transcript.read_text(encoding="utf-8"), 10
        )
    query_text_counts: Dict[str, int] = {}
    counts: Dict[str, int] = {}
    source_file = batch / "inputs" / "path-sources.tsv"
    sources = read_path_sources(source_file)
    sources_by_instance = {"%02d" % index: source for index, source in enumerate(sources)}
    expected_path_selection = {
        "count": 10,
        "method": sources[0].selection_method,
        "seed": sources[0].selection_seed,
        "source_file": "inputs/path-sources.tsv",
    }
    if metadata.get("path_source_selection") != expected_path_selection:
        raise WorkloadError("workload metadata and path-source selection evidence disagree")
    inputs = metadata.get("inputs")
    if not isinstance(inputs, dict):
        raise WorkloadError("workload metadata has no input inventory")
    for name in ("model", "state"):
        item = inputs.get(name)
        if not isinstance(item, dict) or set(item) != {"file", "bytes", "lines"}:
            raise WorkloadError("workload metadata has an invalid %s input record" % name)
        relative = Path(str(item["file"]))
        target = batch / relative
        if relative.is_absolute() or ".." in relative.parts or not target.is_file():
            raise WorkloadError("workload metadata points outside the batch")
        if item["bytes"] != target.stat().st_size or item["lines"] != _line_count(target):
            raise WorkloadError("frozen %s input byte/line count changed" % name)
    for row in rows:
        if None in row or any(value is None for value in row.values()):
            raise WorkloadError("query-list.tsv contains a malformed row")
        relative = Path(row["query_file"])
        if relative.is_absolute() or ".." in relative.parts:
            raise WorkloadError("query path escapes the batch: %s" % relative)
        target = batch / relative
        if not target.is_file() or target.stat().st_size == 0:
            raise WorkloadError("query file is missing or empty: %s" % target)
        text = target.read_text(encoding="utf-8")
        try:
            recorded_bytes = int(row["query_bytes"])
            recorded_lines = int(row["query_lines"])
        except ValueError as exc:
            raise WorkloadError("query byte/line count is not an integer") from exc
        if recorded_bytes != target.stat().st_size or recorded_lines != _line_count(target):
            raise WorkloadError("query byte/line count changed: %s" % row["query_id"])
        if not text.lstrip().upper().startswith("SELECT"):
            raise WorkloadError("query is not a SELECT query: %s" % row["query_id"])
        if "%v" in text or "__SOURCE_IRI__" in text:
            raise WorkloadError("query still contains an uninstantiated placeholder: %s" % row["query_id"])
        query_text_counts[text] = query_text_counts.get(text, 0) + 1
        counts[row["template"]] = counts.get(row["template"], 0) + 1
        if row["query_id"] != row["template"] + "-" + row["instance"]:
            raise WorkloadError("query ID does not match template and instance columns")
        for field in ("template_file", "state_file"):
            value = row[field]
            if value == "not-applicable":
                continue
            referenced = Path(value)
            if referenced.is_absolute() or ".." in referenced.parts or not (batch / referenced).is_file():
                raise WorkloadError("invalid %s reference for %s" % (field, row["query_id"]))
        if row["template"] in BOUND_PATH_TEMPLATES:
            source = sources_by_instance.get(row["instance"])
            if source is None or row["source_iri"] != source.iri:
                raise WorkloadError("bound path query does not match its frozen source row")
            if ("<" + source.iri + ">") not in text:
                raise WorkloadError("bound path source is absent from concrete query text")
            if (
                row["class"] != "P"
                or row["source_kind"] != "property-path-extension"
                or row["generator_version"] != "not-applicable"
                or row["generator_seed"] != "not-applicable"
                or row["selection_seed"] != source.selection_seed
                or row["recurrence_factor"] != "not-applicable"
            ):
                raise WorkloadError("bound path query has inconsistent provenance columns")
        elif row["template"] == ALL_PAIRS_PATH_TEMPLATE:
            if (
                row["instance"] != "00"
                or row["class"] != "P"
                or bool(row["source_iri"])
                or row["source_kind"] != "property-path-extension-all-pairs"
                or any(row[field] != "not-applicable" for field in (
                    "generator_version", "generator_seed", "selection_seed", "recurrence_factor"
                ))
            ):
                raise WorkloadError("all-pairs path query has inconsistent provenance columns")
        elif row["template"] in NON_PATH_TEMPLATES:
            expected_source_kind = (
                "watdiv-official-template"
                if row["template"] in OFFICIAL_TEMPLATES
                else "reconstructed-optional-extension"
            )
            if (
                row["class"] != row["template"][0]
                or bool(row["source_iri"])
                or row["source_kind"] != expected_source_kind
                or row["generator_version"] != "0.6"
                or row["generator_seed"] != NO_EXPOSED_SEED
                or row["selection_seed"] != "not-applicable"
                or row["recurrence_factor"] != "1"
            ):
                raise WorkloadError("non-path query has inconsistent generator columns")
            expected_query = generated_by_template[row["template"]][int(row["instance"])]
            if text != expected_query:
                raise WorkloadError(
                    "query differs from its frozen WatDiv emission: %s" % row["query_id"]
                )
        elif row["source_iri"]:
            raise WorkloadError("non-bound-path query unexpectedly records a source IRI")
    expected_counts = {template: 10 for template in NON_PATH_TEMPLATES + BOUND_PATH_TEMPLATES}
    expected_counts[ALL_PAIRS_PATH_TEMPLATE] = 1
    if counts != expected_counts:
        raise WorkloadError("per-template query counts do not match the formal workload")
    partials = [path for path in batch.rglob("*") if path.name.endswith(".partial")]
    if partials:
        raise WorkloadError("batch contains incomplete artifacts")
    return {
        "schema": "watdiv-10m-workload-audit-v1",
        "status": "ok",
        "query_count": len(rows),
        "non_path_query_count": EXPECTED_NON_PATH,
        "path_query_count": EXPECTED_PATH,
        "template_count": len(counts),
        "distinct_query_texts": len(query_text_counts),
        "repeated_query_instances": len(rows) - len(query_text_counts),
    }


def freeze_workload(
    *,
    watdiv: Path,
    model: Path,
    state: Path,
    official_testsuite: Path,
    path_sources_file: Path,
    output: Path,
    dataset_id: str,
    generator_version: str = "0.6",
    runner: GeneratorRunner = _default_runner,
    make_read_only: bool = True,
) -> Dict[str, Any]:
    """Generate a new immutable 281-query batch without touching old assets."""
    paths = (watdiv, model, state, official_testsuite, path_sources_file, output)
    watdiv, model, state, official_testsuite, path_sources_file, output = (
        path.resolve() for path in paths
    )
    if not watdiv.is_file():
        raise WorkloadError("WatDiv executable/wrapper does not exist: %s" % watdiv)
    if generator_version != "0.6":
        raise WorkloadError("the formal workload requires WatDiv generator version 0.6")
    if not official_testsuite.is_dir():
        raise WorkloadError("official WatDiv testsuite directory does not exist")
    if not dataset_id.strip() or "\t" in dataset_id or "\n" in dataset_id:
        raise WorkloadError("dataset-id must be a non-empty single-line identifier")
    if output.exists():
        raise FileExistsError("refusing to overwrite workload batch: %s" % output)
    staging = output.with_name(output.name + ".partial-%d" % os.getpid())
    if staging.exists():
        raise FileExistsError("staging directory already exists: %s" % staging)

    source_state_before = state.read_bytes()
    sources = read_path_sources(path_sources_file)
    inputs = staging / "inputs"
    template_inputs = inputs / "templates"
    _copy_exact(model, inputs / "wsdbm-data-model.txt")
    _copy_exact(state, inputs / "saved.txt")
    _copy_exact(path_sources_file, inputs / "path-sources.tsv")
    bundled = _path_template_directory()
    for template in OFFICIAL_TEMPLATES:
        _copy_exact(official_testsuite / (template + ".txt"), template_inputs / (template + ".txt"))
    for template in OPTIONAL_TEMPLATES:
        _copy_exact(bundled / (template + ".txt"), template_inputs / (template + ".txt"))
    for template in BOUND_PATH_TEMPLATES + (ALL_PAIRS_PATH_TEMPLATE,):
        _copy_exact(bundled / (template + ".rq.in"), template_inputs / (template + ".rq.in"))

    frozen_state_before = (inputs / "saved.txt").read_bytes()
    records: List[QueryRecord] = []
    for template in NON_PATH_TEMPLATES:
        template_file = template_inputs / (template + ".txt")
        command = (
            str(watdiv),
            "-q",
            str(inputs / "wsdbm-data-model.txt"),
            str(template_file),
            "10",
            "1",
        )
        stdout, stderr = runner(command, inputs)
        try:
            generated = stdout.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise WorkloadError("WatDiv output is not valid UTF-8 for %s" % template) from exc
        _write_text(staging / "generation" / (template + ".generated.rq"), generated)
        (staging / "generation").mkdir(parents=True, exist_ok=True)
        (staging / "generation" / (template + ".stderr.txt")).write_bytes(stderr)
        queries = split_generated_queries(generated, 10)
        source_kind = (
            "watdiv-official-template"
            if template in OFFICIAL_TEMPLATES
            else "reconstructed-optional-extension"
        )
        for instance, query in enumerate(queries):
            relative = Path("queries") / "nonpath" / template / ("%02d.rq" % instance)
            _write_text(staging / relative, query)
            records.append(QueryRecord(
                "%s-%02d" % (template, instance),
                template[0],
                template,
                "%02d" % instance,
                relative.as_posix(),
                str(len(query.encode("utf-8"))),
                str(query.count("\n")),
                source_kind,
                generator_version,
                NO_EXPOSED_SEED,
                "not-applicable",
                "1",
                (Path("inputs") / "templates" / (template + ".txt")).as_posix(),
                "inputs/saved.txt",
                "",
            ))

    for template in BOUND_PATH_TEMPLATES:
        template_text = (template_inputs / (template + ".rq.in")).read_text(encoding="utf-8")
        for instance, source in enumerate(sources):
            relative = Path("queries") / "path" / template / ("%02d.rq" % instance)
            query = _instantiate_path(template_text, source)
            _write_text(staging / relative, query)
            records.append(QueryRecord(
                "%s-%02d" % (template, instance),
                "P",
                template,
                "%02d" % instance,
                relative.as_posix(),
                str(len(query.encode("utf-8"))),
                str(query.count("\n")),
                "property-path-extension",
                "not-applicable",
                "not-applicable",
                sources[0].selection_seed,
                "not-applicable",
                (Path("inputs") / "templates" / (template + ".rq.in")).as_posix(),
                "inputs/path-sources.tsv",
                source.iri,
            ))
    all_pairs_text = (template_inputs / (ALL_PAIRS_PATH_TEMPLATE + ".rq.in")).read_text(
        encoding="utf-8"
    )
    all_pairs_relative = Path("queries") / "path" / ALL_PAIRS_PATH_TEMPLATE / "00.rq"
    all_pairs_query = _instantiate_path(all_pairs_text, None)
    _write_text(staging / all_pairs_relative, all_pairs_query)
    records.append(QueryRecord(
        ALL_PAIRS_PATH_TEMPLATE + "-00",
        "P",
        ALL_PAIRS_PATH_TEMPLATE,
        "00",
        all_pairs_relative.as_posix(),
        str(len(all_pairs_query.encode("utf-8"))),
        str(all_pairs_query.count("\n")),
        "property-path-extension-all-pairs",
        "not-applicable",
        "not-applicable",
        "not-applicable",
        "not-applicable",
        (Path("inputs") / "templates" / (ALL_PAIRS_PATH_TEMPLATE + ".rq.in")).as_posix(),
        "not-applicable",
        "",
    ))

    if (inputs / "saved.txt").read_bytes() != frozen_state_before:
        raise WorkloadError("WatDiv query generation modified the frozen saved.txt")
    if state.read_bytes() != source_state_before:
        raise WorkloadError("WatDiv query generation modified the source saved.txt")
    records.sort(key=lambda record: record.query_id)
    _write_query_list(staging / "query-list.tsv", records)
    metadata = {
        "schema": "watdiv-10m-workload-v1",
        "dataset_id": dataset_id,
        "generator": {
            "name": "WatDiv",
            "version": generator_version,
            "query_count_per_non_path_template": 10,
            "recurrence_factor": 1,
            "duplicate_policy": "preserve-emitted-instances-in-order",
            "seed": NO_EXPOSED_SEED,
            "executable_or_wrapper": str(watdiv),
        },
        "counts": {
            "official_templates": len(OFFICIAL_TEMPLATES),
            "optional_extension_templates": len(OPTIONAL_TEMPLATES),
            "path_templates": 4,
            "non_path_queries": EXPECTED_NON_PATH,
            "path_queries": EXPECTED_PATH,
            "queries": EXPECTED_TOTAL,
        },
        "path_source_selection": {
            "count": len(sources),
            "method": sources[0].selection_method,
            "seed": sources[0].selection_seed,
            "source_file": "inputs/path-sources.tsv",
        },
        "inputs": {
            "model": {"file": "inputs/wsdbm-data-model.txt", "bytes": (inputs / "wsdbm-data-model.txt").stat().st_size, "lines": _line_count(inputs / "wsdbm-data-model.txt")},
            "state": {"file": "inputs/saved.txt", "bytes": (inputs / "saved.txt").stat().st_size, "lines": _line_count(inputs / "saved.txt")},
        },
        "old_repository_queries_modified": False,
        "read_only_files": bool(make_read_only),
    }
    _assert_no_digest_fields(metadata)
    _write_json(staging / "workload.json", metadata)
    audit = audit_workload(staging)
    _write_json(staging / "audit.json", audit)
    if make_read_only:
        read_only = stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH
        for path in staging.rglob("*"):
            if path.is_file():
                path.chmod(read_only)
    output.parent.mkdir(parents=True, exist_ok=True)
    os.replace(staging, output)
    return audit


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    generate = subparsers.add_parser("generate", help="create a new formal workload batch")
    generate.add_argument("--watdiv", required=True, type=Path)
    generate.add_argument("--model", required=True, type=Path)
    generate.add_argument("--state", required=True, type=Path, help="matching saved.txt")
    generate.add_argument("--official-testsuite", required=True, type=Path)
    generate.add_argument("--path-sources", required=True, type=Path)
    generate.add_argument("--dataset-id", required=True)
    generate.add_argument("--out", required=True, type=Path)
    generate.add_argument("--generator-version", default="0.6")
    audit_parser = subparsers.add_parser("audit", help="audit an existing frozen batch")
    audit_parser.add_argument("batch", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "generate":
            result = freeze_workload(
                watdiv=args.watdiv,
                model=args.model,
                state=args.state,
                official_testsuite=args.official_testsuite,
                path_sources_file=args.path_sources,
                output=args.out,
                dataset_id=args.dataset_id,
                generator_version=args.generator_version,
            )
        else:
            result = audit_workload(args.batch.resolve())
    except (OSError, WorkloadError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
