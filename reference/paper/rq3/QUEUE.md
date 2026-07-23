# Autonomous experiment queue — RQ3/RQ2 campaign (started 2026-07-23, user away)

## DRIVER PROTOCOL (follow on every background-task completion)
1. Read THIS file. Identify the item marked RUNNING; analyze its output CSVs (coverage per method; for cross-engine items, byte-identity of `circuit_sha256`).
2. If GraphDB is down (`curl -s http://localhost:7200/rest/repositories` fails), RESTART it (below) and delete any new `graphdb-home/logs/*.hprof`.
3. PUSH results+logs: copy CSVs+`.log`+`.progress` into `reference/paper/{rq3|rq2}/<name>/`; `git add` them, `git add -f` the `.log`s (gitignored); commit; `git push origin feat/rdfstar-reification`. (Circuits stay out of git.)
4. Mark the item DONE here (Edit), commit+push this file, and LAUNCH the next PENDING item as a `run_in_background` job.
5. Continue until all DONE or the user interrupts. Do NOT stop on your own. Make sensible calls on new snags, document them here.

## COMMON
- `REPO=/mnt/nfs/home/ac145595/workspace/SPARQLcirc/SPARQLcirc`; `ART=/mnt/nfs/home/ac145595/workspace/rq3-artifacts`
- `JBIN=/mnt/nfs/home/ac145595/.conda/envs/sparqlcirc/lib/jvm/bin/java`
- Run from `$REPO/reference/paper`; each run exports: `PCM_JAVA_BIN=$JBIN`, `PCM_BATCH_ID=$(python3 -c "import secrets;print(secrets.token_hex(32))")`, `PCM_CIRCUIT_CACHE_DIR=$ART/circuits/<name>`, `--exploratory`.
- Endpoints: `_DEFAULT_ENDPOINTS` is correct (graphdb watdiv / watdivbase / watdiv100m / watdiv100mbase). No endpoint env vars needed. (Ignore the spec's `watdiv10m` names.)
- RESTART GraphDB: `JAVA_HOME=/mnt/nfs/home/ac145595/.conda/envs/sparqlcirc/lib/jvm PATH=$JAVA_HOME/bin:$PATH GDB_HEAP_SIZE=90g GDB_JAVA_OPTS="-Dgraphdb.home=/mnt/nfs/home/ac145595/workspace/graphdb-home" /mnt/nfs/home/ac145595/workspace/tools/graphdb-10.7.6/bin/graphdb -d` then poll 7200.
- Facts: PCM_FORCE_FLAT=1 => flat; unset => factored (writable only; factored applies to BGP classes C,F,L,S — O/M error/fall-back, so factored runs use C,F,L,S). GraphDB at 90g. Idle 10M engines (oxi/qlever/mdb) currently stopped.

## ITEMS
### Q1 [RUNNING bbfcudz7x] GraphDB 100M aggressive — flat, single-run, 3600s
F,L,M,O,S then C isolated; PCM_FORCE_FLAT=1; circuits->graphdb-100m. on-done: push to `reference/paper/rq3/graphdb-100m/`. Also provides 100M flat node counts (v9) for RQ2.

### Q2 [PENDING] RQ2 flat node counts @10M (my 10M matrices were v8 = no node counts)
`PCM_FORCE_FLAT=1 PCM_CIRCUIT_CACHE_DIR=$ART/circuits/rq2-flat-10m timeout 7200 python3 paper_construction_matrix.py --exploratory --engines graphdb --scales 10M --classes C,F,L,O,S --methods N,C --warmups 0 --runs 1 --out $ART/nodecount_flat_10m.csv` → push to `reference/paper/rq2/`.

### Q3 [PENDING] RQ2 factored @10M (writable, NO force-flat, BGP classes only)
`unset PCM_FORCE_FLAT; PCM_CIRCUIT_CACHE_DIR=$ART/circuits/rq2-factored-10m timeout 10800 python3 paper_construction_matrix.py --exploratory --engines graphdb --scales 10M --classes C,F,L,S --methods C --warmups 0 --runs 1 --out $ART/nodecount_factored_10m.csv` → push.

### Q4 [PENDING] RQ2 factored @100M (writable; F,L,S then C isolated for C3 OOM)
`unset PCM_FORCE_FLAT; PCM_CIRCUIT_CACHE_DIR=$ART/circuits/rq2-factored-100m timeout 28800 python3 paper_construction_matrix.py --exploratory --engines graphdb --scales 100M --classes F,L,S --methods C --warmups 0 --runs 1 --out $ART/nodecount_factored_100m_fls.csv`; then same with `--classes C ... --out $ART/nodecount_factored_100m_c.csv` → push.

### Q5 [PENDING] Assemble + validate + push
Build `reference/paper/nodecount_flat_10m_100m.csv` (10M from Q2 + 100M C,F,L,O,S from Q1's `graphdb_100m_*.csv`) and `nodecount_factored_10m_100m.csv` (Q3+Q4). Node = leaves+⊗+⊕+⊖; NPCS = `npcs_token_occurrences+npcs_oplus+npcs_ominus+npcs_leaves`; flat/factored = `structure_signature.nodes`. Validate NPCS leaf tokenizer once (spec E-C1c). Push both CSVs to `reference/paper/`. Then queue COMPLETE — stop until user returns.

## OPTIONAL TAIL (only if time; not yet requested)
- Other engines (Oxigraph/QLever/MDB) @100M for cross-engine byte-identity at scale (restart each, run flat single-run). Skip unless clearly wanted.
