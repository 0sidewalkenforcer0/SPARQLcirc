# WatDiv 10M evaluation templates

`O1.txt`–`O5.txt` are reconstructed OPTIONAL extensions rather than templates
distributed by the upstream WatDiv project. Workload metadata and result tables
identify them separately from WatDiv's official 20-template workload.

The `P-*.rq.in` files are repository-specific property-path extensions. The
first three contain one `__SOURCE_IRI__` marker. `P-plus-all.rq.in` represents a
single all-pairs query and appears once in the 281-query workload.

The ten bound path sources are stored as an external frozen input because their
selection depends on statistics from the exact deduplicated 10M graph. The
source table contains ten distinct rows with this schema:

```text
source_id	iri	stratum	reachable_count	max_hops	selection_method	selection_seed
```

These sources are part of the new 281-query evaluation workload. The older
frozen query files under `reference/paper/queries/watdiv` remain historical
regression and reproduction inputs.
