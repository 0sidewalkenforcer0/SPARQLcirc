#!/usr/bin/env python3
"""Sample Linux process-tree RSS and attribute peaks to named stages.

The experiment runners need an instantaneous stage peak, not ``ru_maxrss``:
the latter is a process-lifetime high-water mark and makes every later stage
inherit an earlier peak.  This module has no third-party dependencies and uses
Linux ``/proc`` because the evaluation cluster runs Linux.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import threading
from typing import Any, Dict, Iterator, Optional, Set


SCHEMA = "sparqlcirc-stage-rss-v1"


def _rss_bytes(pid: int) -> Optional[int]:
    try:
        text = (Path("/proc") / str(pid) / "status").read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("VmRSS:"):
            fields = line.split()
            if len(fields) >= 2:
                try:
                    return int(fields[1]) * 1024
                except ValueError:
                    return None
    return None


def _children(pid: int) -> Set[int]:
    path = Path("/proc") / str(pid) / "task" / str(pid) / "children"
    try:
        return {int(value) for value in path.read_text(encoding="ascii").split()}
    except (OSError, ValueError):
        return set()


def process_tree_rss_bytes(root_pid: int) -> Optional[int]:
    """Return current aggregate RSS for a process and its live descendants."""
    pending = [int(root_pid)]
    seen: Set[int] = set()
    total = 0
    observed = False
    while pending:
        pid = pending.pop()
        if pid in seen:
            continue
        seen.add(pid)
        value = _rss_bytes(pid)
        if value is not None:
            observed = True
            total += value
        pending.extend(_children(pid) - seen)
    return total if observed else None


class StageRssSampler:
    """Periodically sample one process tree under an explicitly named stage."""

    def __init__(self, root_pid: Optional[int] = None, interval_s: float = 0.05) -> None:
        if interval_s <= 0:
            raise ValueError("RSS sampling interval must be positive")
        self.root_pid = int(os.getpid() if root_pid is None else root_pid)
        self.interval_s = float(interval_s)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._stage: Optional[str] = None
        self._segment_start: Optional[int] = None
        self._stages: Dict[str, Dict[str, Any]] = {}
        self._observed = False

    def start(self) -> "StageRssSampler":
        if self._thread is not None:
            raise RuntimeError("RSS sampler has already been started")
        self._thread = threading.Thread(
            target=self._sample_loop,
            name="stage-rss-%d" % self.root_pid,
            daemon=True,
        )
        self._thread.start()
        return self

    def _update_locked(self, stage: str, value: int) -> None:
        row = self._stages[stage]
        row["samples"] += 1
        row["rss_end_bytes"] = value
        peak = row.get("peak_rss_bytes")
        row["peak_rss_bytes"] = value if peak is None else max(int(peak), value)
        if self._segment_start is not None:
            delta = max(0, value - self._segment_start)
            row["max_peak_minus_segment_start_bytes"] = max(
                int(row["max_peak_minus_segment_start_bytes"]), delta
            )
        self._observed = True

    def _sample_active(self) -> None:
        value = process_tree_rss_bytes(self.root_pid)
        if value is None:
            return
        with self._lock:
            if self._stage is not None:
                self._update_locked(self._stage, value)

    def _sample_loop(self) -> None:
        while not self._stop.wait(self.interval_s):
            self._sample_active()

    def set_stage(self, stage: Optional[str]) -> None:
        if self._thread is None:
            raise RuntimeError("RSS sampler must be started before setting a stage")
        value = process_tree_rss_bytes(self.root_pid)
        with self._lock:
            if self._stage is not None and value is not None:
                self._update_locked(self._stage, value)
            self._stage = stage
            self._segment_start = value
            if stage is not None:
                row = self._stages.setdefault(stage, {
                    "segments": 0,
                    "samples": 0,
                    "rss_start_bytes": value,
                    "rss_end_bytes": value,
                    "peak_rss_bytes": value,
                    "max_peak_minus_segment_start_bytes": 0,
                })
                row["segments"] += 1
                if row["rss_start_bytes"] is None:
                    row["rss_start_bytes"] = value
                if value is not None:
                    self._update_locked(stage, value)

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        self.set_stage(name)
        try:
            yield
        finally:
            self.set_stage(None)

    def finish(self) -> Dict[str, Any]:
        if self._thread is None:
            raise RuntimeError("RSS sampler was not started")
        self.set_stage(None)
        self._stop.set()
        self._thread.join(timeout=max(1.0, self.interval_s * 4.0))
        return {
            "schema": SCHEMA,
            "available": self._observed,
            "root_pid": self.root_pid,
            "sample_interval_ms": self.interval_s * 1000.0,
            "scope": "instantaneous aggregate RSS of the root process and live descendants",
            "source": "Linux /proc VmRSS",
            "stages": {
                key: dict(value) for key, value in sorted(self._stages.items())
            },
        }


def stage_peak_rss_bytes(result: Dict[str, Any], stage: str) -> Optional[int]:
    stages = result.get("stages")
    if not isinstance(stages, dict):
        return None
    row = stages.get(stage)
    if not isinstance(row, dict) or row.get("peak_rss_bytes") is None:
        return None
    return int(row["peak_rss_bytes"])
