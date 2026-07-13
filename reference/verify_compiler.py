"""Offline regression for the production multi-root compiler contract."""
from __future__ import annotations

import compiler
import compile_bdd


CIRCUIT = {
    "x": ("leaf", "urn:test:x"),
    "y": ("leaf", "urn:test:y"),
    "z": ("leaf", "urn:test:z"),
    "shared": ("times", ("x", "y")),
    "a": ("plus", ("shared", "z")),
    "b": ("minus", ("shared", "z")),
    "one": ("const", 1),
    "not-x": ("minus", ("one", "x")),
    "zero": ("const", 0),
}
ROOTS = {
    "answer-a": "a",
    "answer-b": "b",
    "duplicate-a": "a",
    "not-x": "not-x",
    "truth": "one",
    "falsehood": "zero",
}
WEIGHTS = {"urn:test:x": 0.2, "urn:test:y": 0.6, "urn:test:z": 0.7}


def _close(left, right, label):
    if abs(left - right) >= 1e-12:
        raise AssertionError("%s: %.17g != %.17g" % (label, left, right))


def _truth():
    return {
        key: compile_bdd.wmc_enum(CIRCUIT, root, WEIGHTS)
        for key, root in ROOTS.items()
    }


def _check_batch(batch, expected, backend, mode):
    if set(batch.roots) != set(ROOTS):
        raise AssertionError("root map changed: %r" % (batch.roots,))
    got = batch.wmc_many(WEIGHTS)
    for key in expected:
        _close(got[key], expected[key], "%s/%s/%s" % (backend, mode, key))
    if batch.metrics["backend"] != backend or batch.metrics["mode"] != mode:
        raise AssertionError("wrong compiler metadata: %r" % (batch.metrics,))
    expected_managers = 1 if mode == "shared" else len(ROOTS)
    if batch.metrics["manager_count"] != expected_managers:
        raise AssertionError("wrong manager count: %r" % (batch.metrics,))
    if batch.metrics["compiled_nodes_unique"] > batch.metrics["compiled_nodes_sum_roots"]:
        raise AssertionError("unique node count exceeds sum of root sizes")
    if batch.metrics["wmc_visited_nodes"] > batch.metrics["compiled_nodes_sum_roots"]:
        raise AssertionError("batch WMC visited too many physical nodes")
    if backend == "cudd":
        peak_sum = batch.metrics["manager_peak_live_nodes"]
        peak_max = batch.metrics["manager_peak_live_nodes_max"]
        if peak_sum < peak_max or batch.metrics["manager_current_nodes"] < 0:
            raise AssertionError("invalid aggregate manager metrics: %r" % (batch.metrics,))
        if mode == "shared" and peak_sum != peak_max:
            raise AssertionError("one shared manager must have identical sum/max peaks")
    if mode == "shared" and batch.metrics["sharing_savings_nodes"] <= 0:
        raise AssertionError("shared manager did not reuse any cross-root BDD node")
    if mode == "per-root" and batch.metrics["sharing_savings_nodes"] != 0:
        raise AssertionError("per-root managers reported impossible cross-manager sharing")
    sizes = batch.root_sizes()
    if set(sizes) != set(ROOTS) or any(size < 0 for size in sizes.values()):
        raise AssertionError("invalid per-root sizes: %r" % (sizes,))
    return got


def _check_backend(backend, expected):
    empty = compiler.compile_many(CIRCUIT, {}, backend=backend)
    if (empty.roots or empty.wmc_many(WEIGHTS)
            or empty.metrics["root_count"] != 0
            or empty.metrics["manager_count"] != 0):
        raise AssertionError("empty root vector was not preserved")

    batches = {}
    for mode in ("shared", "per-root"):
        batch = compiler.compile_many(CIRCUIT, ROOTS, mode=mode, backend=backend)
        batches[mode] = batch
        _check_batch(batch, expected, backend, mode)
    for key in expected:
        shared = batches["shared"].wmc_many(WEIGHTS)[key]
        per_root = batches["per-root"].wmc_many(WEIGHTS)[key]
        _close(shared, per_root, "%s mode parity/%s" % (backend, key))
    if batches["shared"].metrics["order_sha256"] != batches["per-root"].metrics["order_sha256"]:
        raise AssertionError("compile modes did not derive the same global order")
    if backend == "cudd":
        # `not-x` is represented by a complemented CUDD edge.  This assertion
        # catches treating succ(~x) as if it had propagated the complement.
        _close(batches["shared"].wmc_many(WEIGHTS)["not-x"], 0.8,
               "CUDD complemented-edge WMC")
        if batches["shared"].metrics["manager_reorderings"] != 0:
            raise AssertionError("fixed-order production mode reordered variables")


def _check_deep_cudd():
    depth = 2100
    circ = {"one": ("const", 1)}
    previous = "one"
    weights = {}
    for index in range(depth):
        leaf = "leaf-%04d" % index
        token = "urn:test:deep:%04d" % index
        gate = "gate-%04d" % index
        circ[leaf] = ("leaf", token)
        circ[gate] = ("times", (previous, leaf))
        weights[token] = 0.999
        previous = gate
    batch = compiler.compile_many(circ, {"deep": previous}, backend="cudd")
    probability = batch.wmc_many(weights)["deep"]
    _close(probability, 0.999 ** depth, "CUDD iterative WMC depth>%d" % 2000)
    if batch.metrics["wmc_visited_nodes"] != depth:
        raise AssertionError("deep WMC did not visit exactly one node per variable: %r"
                             % (batch.metrics,))


def _negative_checks():
    try:
        compiler.compile_many(CIRCUIT, ROOTS, backend="oracle", order=("urn:test:x",))
    except ValueError as exc:
        if "omits" not in str(exc):
            raise
    else:
        raise AssertionError("incomplete variable order was accepted")

    cyclic = {"loop": ("times", ("loop",))}
    try:
        compiler.compile_many(cyclic, {"answer": "loop"}, backend="oracle")
    except ValueError as exc:
        if "cycle" not in str(exc):
            raise
    else:
        raise AssertionError("cyclic circuit was accepted")

    batch = compiler.compile_many(CIRCUIT, ROOTS, backend="oracle")
    try:
        batch.wmc_many({"urn:test:x": 0.2})
    except KeyError as exc:
        if "missing probability" not in str(exc):
            raise
    else:
        raise AssertionError("missing probability was accepted")

    for bad in (True, -0.1, 1.1, float("nan")):
        invalid = dict(WEIGHTS)
        invalid["urn:test:x"] = bad
        try:
            batch.wmc_many(invalid)
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError("invalid probability was accepted: %r" % (bad,))


def main():
    order = compiler.deterministic_order(CIRCUIT, ROOTS)
    if order != ("urn:test:x", "urn:test:y", "urn:test:z"):
        raise AssertionError("unexpected deterministic order: %r" % (order,))
    reversed_roots = dict(reversed(list(ROOTS.items())))
    if compiler.deterministic_order(CIRCUIT, reversed_roots) != order:
        raise AssertionError("variable order depends on root insertion order")

    expected = _truth()
    _check_backend("oracle", expected)
    _negative_checks()
    try:
        _check_backend("cudd", expected)
        _check_deep_cudd()
    except compiler.BackendUnavailable as exc:
        print("compiler: oracle OK; CUDD unavailable (%s)" % (exc,))
    else:
        print("compiler: oracle + CUDD shared/per-root/complement parity ALL OK")


if __name__ == "__main__":
    main()
