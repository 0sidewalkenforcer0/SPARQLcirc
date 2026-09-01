#!/usr/bin/env python3
"""Remove interrupted circuit state and revalidate a dedicated WDBench store."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Dict, Optional, Sequence
import urllib.error
import urllib.parse
import urllib.request

import validate_stores


SCHEMA = "wdbench-graphdb-store-recovery-v1"
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

CLEANUP_UPDATE = """DELETE { GRAPH ?graph { ?subject ?predicate ?object } }
WHERE {
  GRAPH ?graph { ?subject ?predicate ?object }
  FILTER(STRSTARTS(STR(?graph), "urn:circuit:run:"))
};
DELETE { ?subject ?predicate ?object }
WHERE {
  {
    SELECT DISTINCT ?subject WHERE {
      {
        VALUES ?anchor {
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
        ?subject ?anchor ?value .
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
    }
  }
  ?subject ?predicate ?object .
}"""


class RecoveryError(RuntimeError):
    """The endpoint could not be cleaned or did not validate afterwards."""


def _positive_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise argparse.ArgumentTypeError("value must be positive and finite")
    return result


def _post_update(update_endpoint: str, update: str, timeout_s: float) -> int:
    body = urllib.parse.urlencode({"update": update}).encode("utf-8")
    request = urllib.request.Request(
        update_endpoint,
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"},
    )
    try:
        with _OPENER.open(request, timeout=timeout_s) as response:
            response.read()
            return int(response.status)
    except urllib.error.HTTPError as error:
        detail = error.read(4096).decode("utf-8", errors="replace")
        raise RecoveryError("cleanup HTTP %d: %s" % (error.code, detail.strip())) from error
    except urllib.error.URLError as error:
        raise RecoveryError("cleanup endpoint request failed: %s" % error) from error


def recover(
    metadata: Path,
    base_endpoint: str,
    mixed_endpoint: str,
    update_endpoint: str,
    timeout_s: float,
) -> Dict[str, Any]:
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise RecoveryError("timeout must be positive and finite")
    cleanup_status = _post_update(update_endpoint, CLEANUP_UPDATE, timeout_s)
    validation = validate_stores.validate(
        metadata, base_endpoint, mixed_endpoint, timeout_s
    )
    return {
        "schema": SCHEMA,
        "status": "ok" if validation.get("status") == "ok" else "invalid",
        "cleanup_http_status": cleanup_status,
        "base_endpoint": base_endpoint,
        "mixed_endpoint": mixed_endpoint,
        "update_endpoint": update_endpoint,
        "validation": validation,
    }


def _atomic_json(path: Path, value: Any) -> None:
    if path.exists():
        raise RecoveryError("refusing to overwrite %s" % path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False, newline="\n"
    ) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", required=True, type=Path)
    parser.add_argument("--base-endpoint", required=True)
    parser.add_argument("--mixed-endpoint", required=True)
    parser.add_argument("--update-endpoint", required=True)
    parser.add_argument("--timeout", type=_positive_float, default=5000.0)
    parser.add_argument("--out", required=True, type=Path)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = recover(
            args.metadata.resolve(),
            args.base_endpoint,
            args.mixed_endpoint,
            args.update_endpoint,
            args.timeout,
        )
        _atomic_json(args.out.resolve(), result)
    except (OSError, UnicodeError, ValueError, RecoveryError, validate_stores.ValidationError) as error:
        print("error: %s" % error, file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
