#!/usr/bin/env python3
"""Incremental helpers for SPARQL Results TSV responses.

TSV keeps one solution mapping on one physical line.  Tabs and newlines inside
RDF literals are escaped by the SPARQL result format, so a caller can count and
process rows without retaining the complete endpoint response.
"""
from __future__ import annotations

import re
from typing import Iterator, List, Sequence, Tuple


XSD = "http://www.w3.org/2001/XMLSchema#"
XSD_STRING = XSD + "string"
XSD_BOOLEAN = XSD + "boolean"
XSD_INTEGER = XSD + "integer"
XSD_DECIMAL = XSD + "decimal"
XSD_DOUBLE = XSD + "double"
RDF_LANGSTRING = "http://www.w3.org/1999/02/22-rdf-syntax-ns#langString"

_INTEGER = re.compile(r"[+-]?[0-9]+")
_DECIMAL = re.compile(r"[+-]?(?:[0-9]+\.[0-9]*|\.[0-9]+)")
_DOUBLE = re.compile(
    r"[+-]?(?:(?:[0-9]+\.[0-9]*|\.[0-9]+|[0-9]+)[eE][+-]?[0-9]+)"
)


class TsvResultsError(ValueError):
    """A streamed endpoint response is not valid SPARQL Results TSV."""


class TsvLineStream:
    """Split an arbitrary byte-chunk stream into strict UTF-8 TSV lines."""

    def __init__(self) -> None:
        self._pending = bytearray()

    def feed(self, chunk: bytes) -> Iterator[str]:
        if not chunk:
            return
        self._pending.extend(chunk)
        start = 0
        while True:
            newline = self._pending.find(b"\n", start)
            if newline < 0:
                if start:
                    del self._pending[:start]
                return
            raw = bytes(self._pending[start:newline])
            if raw.endswith(b"\r"):
                raw = raw[:-1]
            try:
                yield raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise TsvResultsError("response contains a non-UTF-8 TSV line") from exc
            start = newline + 1

    def finish(self) -> Iterator[str]:
        if not self._pending:
            return
        raw = bytes(self._pending)
        self._pending.clear()
        if raw.endswith(b"\r"):
            raw = raw[:-1]
        try:
            yield raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise TsvResultsError("response contains a non-UTF-8 final TSV line") from exc


def parse_header(line: str) -> List[str]:
    fields = line.split("\t")
    if not fields or any(not item.startswith("?") or len(item) == 1 for item in fields):
        raise TsvResultsError("SPARQL Results TSV header must contain ?variable fields")
    variables = [item[1:] for item in fields]
    if len(set(variables)) != len(variables):
        raise TsvResultsError("SPARQL Results TSV header contains duplicate variables")
    return variables


def split_row(line: str, variables: Sequence[str]) -> List[str]:
    fields = line.split("\t")
    if len(fields) != len(variables):
        raise TsvResultsError(
            "TSV result row has %d fields; expected %d" % (len(fields), len(variables))
        )
    return fields


def _unescape(value: str) -> str:
    result: List[str] = []
    index = 0
    escapes = {
        "t": "\t",
        "b": "\b",
        "n": "\n",
        "r": "\r",
        "f": "\f",
        '"': '"',
        "'": "'",
        "\\": "\\",
    }
    while index < len(value):
        character = value[index]
        if character != "\\":
            result.append(character)
            index += 1
            continue
        if index + 1 >= len(value):
            raise TsvResultsError("RDF term ends with an incomplete escape")
        marker = value[index + 1]
        if marker in escapes:
            result.append(escapes[marker])
            index += 2
            continue
        if marker in ("u", "U"):
            width = 4 if marker == "u" else 8
            digits = value[index + 2:index + 2 + width]
            if len(digits) != width or not all(ch in "0123456789abcdefABCDEF" for ch in digits):
                raise TsvResultsError("RDF term contains an invalid Unicode escape")
            codepoint = int(digits, 16)
            try:
                result.append(chr(codepoint))
            except ValueError as exc:
                raise TsvResultsError("RDF term contains an invalid Unicode code point") from exc
            index += 2 + width
            continue
        raise TsvResultsError("RDF term contains an unsupported escape: \\%s" % marker)
    return "".join(result)


def _quoted_end(value: str) -> int:
    escaped = False
    for index in range(1, len(value)):
        character = value[index]
        if escaped:
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == '"':
            return index
    raise TsvResultsError("TSV literal has no closing quote")


def term_key(value: str, allow_bare_text: bool = False) -> List[str]:
    """Return the canonical term representation used by the experiment runner."""
    if value == "":
        return ["unbound"]
    if value.startswith("<") and value.endswith(">"):
        return ["iri", _unescape(value[1:-1])]
    if value.startswith("_:") and len(value) > 2:
        return ["bnode", value[2:]]
    if value.startswith('"'):
        end = _quoted_end(value)
        lexical = _unescape(value[1:end])
        suffix = value[end + 1:]
        if not suffix:
            return ["literal", lexical, XSD_STRING, ""]
        if suffix.startswith("@") and len(suffix) > 1:
            return ["literal", lexical, RDF_LANGSTRING, suffix[1:].lower()]
        if suffix.startswith("^^<") and suffix.endswith(">"):
            return ["literal", lexical, _unescape(suffix[3:-1]), ""]
        raise TsvResultsError("TSV literal has an invalid language/datatype suffix")
    if value in ("true", "false"):
        return ["literal", value, XSD_BOOLEAN, ""]
    if _INTEGER.fullmatch(value):
        return ["literal", value, XSD_INTEGER, ""]
    if _DECIMAL.fullmatch(value):
        return ["literal", value, XSD_DECIMAL, ""]
    if _DOUBLE.fullmatch(value):
        return ["literal", value, XSD_DOUBLE, ""]
    if (
        allow_bare_text
        and value
        and not value.startswith(("<", "_:", '"'))
    ):
        return ["literal", _unescape(value), XSD_STRING, ""]
    raise TsvResultsError("unsupported SPARQL Results TSV term: %r" % value[:200])


def literal_lexical(value: str, allow_bare_text: bool = False) -> Tuple[str, str]:
    """Return a literal lexical form and the endpoint encoding that carried it.

    GraphDB emits some expression-produced string bindings as unquoted TSV text.
    This compatibility path is intentionally opt-in.
    """
    try:
        term = term_key(value)
    except TsvResultsError:
        if (
            not allow_bare_text
            or not value
            or value.startswith(("<", "_:", '"'))
        ):
            raise
        return _unescape(value), "bare-text"
    if term[0] != "literal":
        raise TsvResultsError("TSV field is not a literal")
    return term[1], "rdf-literal"


def binding_value(
    fields: Sequence[str],
    variables: Sequence[str],
    allow_bare_text: bool = False,
) -> List[object]:
    if len(fields) != len(variables):
        raise TsvResultsError("binding field count does not match the response header")
    values = dict(zip(variables, fields))
    return [
        [variable, term_key(values[variable], allow_bare_text=allow_bare_text)]
        for variable in sorted(variables)
    ]
