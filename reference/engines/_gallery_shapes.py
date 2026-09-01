"""Single source of truth for the gallery shape sets, shared by the byte-identity harnesses
(verify_http.py, verify_oxigraph.py) so E10's coverage stays == E1's correctness set (verify_gallery.py).

`E1_NONPATH` is the full non-path correctness battery E1 checks WMC == PWE on. E10 diffs byte-identity
over EXACTLY this set, so per-engine correctness = (E10 byte-identity) ∘ (E1 WMC == PWE) with no shape
left uncovered: every engine builds the same circuit as RDF4J for every E1 shape, and that circuit's WMC
== PWE. Keep this list in sync with verify_gallery.py's `answers()` + `RDFLIB_OPS`.

Excluded here: path shapes (pathplus/pathstar/... need the writable iterative protocol -> verified on
writable engines separately), and rejection guards (filter_exists_unsupported/limit/minus_rnested/
opt_xprod, which must ERROR rather than be equality-checked).

Also not yet listed: the FILTER shapes (filter/filter_optional/filter_minus). They are part of E1's
correctness battery in verify_gallery.py, but the frozen paper workload and recorded cross-engine
byte-identity artifacts predate them. Add them here only together with a byte-identity re-run."""

E1_NONPATH = [
    "atom", "join", "union",
    "minus", "minus_disjoint", "minus_union", "minus_p2union", "minus_chain",
    "optional", "opt_left", "opt_right", "opt_disjoint",
    "distinct",
]
