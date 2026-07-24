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
- GOTCHAS: (1) `rm` the `--out` CSV before re-running a class into it, else the harness rejects the resume with `ValueError: noncanonical batch_id identity` (checkpoint expects the same batch_id). (2) Don't string-compare curl'd counts (trailing CR); strip with `tr -dc 0-9` or check reachability only. (3) 100M heavy MINUS/CC3 can OOM even @90g and crash GraphDB → run C (and retries of M) ISOLATED, restart after.

## STATUS: **QUEUE COMPLETE** (2026-07-24) — Q1..Q5 done+pushed. factored 100M: F/L/S 17/17, C empty (CC1 timeout, CC2/3 cleanup — expected). GraphDB survived isolated C. Optional tail: fill transient 10M-F cleanup gaps (Q4b), then idle-engine 100M byte-identity.

## ITEMS
### Q1 [DONE d9e1b6c] GraphDB 100M aggressive — flat, single-run, 3600s
DONE: C 26/30 (F/L/O/S full, MM2[9.7M gates]/MM3[10.4M gates] built, CC1/CC2 built; caps CC3+MM1/MM4/MM5). Pushed to reference/paper/rq3/graphdb-100m/.
F,L,M,O,S then C isolated; PCM_FORCE_FLAT=1; circuits->graphdb-100m. on-done: push to `reference/paper/rq3/graphdb-100m/`. Also provides 100M flat node counts (v9) for RQ2.
- Run1 built F, L, **MM2 (9.7M gates, 827s), MM3 (10.4M gates, 1181s)** then **MM4 OOM-crashed GraphDB @90g** → O,S,C lost (network). MM1 timeout(>3600s).
- Q1b [bb1rddfzc] recovery: restarted GraphDB, running O,S then C isolated → graphdb_100m_os.csv / _c.csv. MM1/MM4/MM5 + C3 recorded as caps (crash-prone/too-heavy; not worth repeated crash-restart). Final 100M = flmos(F,L,MM2,MM3) + os(O,S) + c(C1,C2).

### Q2 [DONE 3d10b14] RQ2 flat node counts @10M (my 10M matrices were v8 = no node counts)
`PCM_FORCE_FLAT=1 PCM_CIRCUIT_CACHE_DIR=$ART/circuits/rq2-flat-10m timeout 7200 python3 paper_construction_matrix.py --exploratory --engines graphdb --scales 10M --classes C,F,L,O,S --methods N,C --warmups 0 --runs 1 --out $ART/nodecount_flat_10m.csv` → push to `reference/paper/rq2/`.

### Q3 [DONE] RQ2 factored @10M (writable, NO force-flat, BGP classes only)
`unset PCM_FORCE_FLAT; PCM_CIRCUIT_CACHE_DIR=$ART/circuits/rq2-factored-10m timeout 10800 python3 paper_construction_matrix.py --exploratory --engines graphdb --scales 10M --classes C,F,L,S --methods C --warmups 0 --runs 1 --out $ART/nodecount_factored_10m.csv` → push.


> **Q3 finding (2026-07-24):** factored is the staged/feedback construction, NOT a compaction. On *bound* WatDiv it emits MORE explicit structure than flat (verified via persisted .nt: same query, flat 55 subj/200 tri vs factored 116/296). So factored >= flat here: SS2 3570 vs 1530, LL1 18 vs 10, CC1 blew to ~997k. C2/C3/FF errored (protocol/cleanup/reap). This is EXPECTED per RQ2 spec ("a few too-large/timeout cells... honest data; leave them empty"). factored's compaction WIN is the separate reconvergence half (unbound_factored_vs_flat.csv, already real). 9 BGP cells clean @10M.
### Q4 [DONE] RQ2 factored @100M (writable; F,L,S then C isolated for C3 OOM)
`unset PCM_FORCE_FLAT; PCM_CIRCUIT_CACHE_DIR=$ART/circuits/rq2-factored-100m timeout 28800 python3 paper_construction_matrix.py --exploratory --engines graphdb --scales 100M --classes F,L,S --methods C --warmups 0 --runs 1 --out $ART/nodecount_factored_100m_fls.csv`; then same with `--classes C ... --out $ART/nodecount_factored_100m_c.csv` → push.

### Q5 [DONE] Assemble + validate + push
Build `reference/paper/nodecount_flat_10m_100m.csv` (10M from Q2 + 100M C,F,L,O,S from Q1's `graphdb_100m_*.csv`) and `nodecount_factored_10m_100m.csv` (Q3+Q4). Node = leaves+⊗+⊕+⊖; NPCS = `npcs_token_occurrences+npcs_oplus+npcs_ominus+npcs_leaves`; flat/factored = `structure_signature.nodes`. Validate NPCS leaf tokenizer once (spec E-C1c). Push both CSVs to `reference/paper/`. Then queue COMPLETE — stop until user returns.

## Q6 [DONE] 100M cross-engine byte-identity
DONE 2026-07-24: Oxigraph-100M vs GraphDB-100M = 21/21 BYTE-IDENTICAL (F5 L4 O5 S7); MINUS timed out on Oxigraph (construction cost, not correctness; writable-pair MINUS identity holds at 10M). Results in rq3/oxigraph-100m/BYTEID_100M.md. Oxigraph stopped, GraphDB restarted after. Construction-cost analysis in rq3/CONSTRUCTION_COST_ENGINE.md.
**GraphDB is STOPPED** (freed 90g for Oxigraph). To resume GraphDB work, restart per COMMON recipe.
Goal: show circuit_sha256 built at 100M on Oxigraph/QLever/MDB == GraphDB-100M (rq3/graphdb-100m/graphdb_100m_assembled.csv, C rows). 100M indexes already exist: oxi-watdiv100m(+base), qlever-watdiv100m(+base), mdb-watdiv100m(+base).
- Driver script: `scratchpad/byteid_oxi_100m.sh <CLASSES>` — stops GraphDB, starts oxi `serve -l <store> -b localhost:7880|7881` (reified|base) via tools/images/oxigraph.sif, smoke-checks counts, runs `--engines oxigraph --scales 100M --methods C --warmups 0 --runs 1` flat (PCM_FORCE_FLAT=1) into $ART/oxi_100m_byteid.csv.
- Endpoints already in _DEFAULT_ENDPOINTS: oxigraph 100M reified=7880/query base=7881/query update=7880/update; qlever 100M=7003/7004; mdb 100M=1236/1237.
- Proof step: F,L. If shas match GraphDB -> expand O,S (+M on writable oxi), then QLever/MDB (read-only: monotone+OPTIONAL only, no MINUS).
- Compare: join oxi_100m_byteid.csv to graphdb_100m_assembled.csv on (class,template), check circuit_sha256 equal.

## OPTIONAL TAIL (only if time; not yet requested)
- Other engines (Oxigraph/QLever/MDB) @100M for cross-engine byte-identity at scale (restart each, run flat single-run). Skip unless clearly wanted.
