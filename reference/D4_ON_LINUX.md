# Running d4 (the compiler-scaling data point) — Linux/x86 only

d4 cannot be built on this Apple-Silicon Mac: its bundled **PATOH** hypergraph
partitioner ships as an **x86_64-only** static library with no arm64 build. So the
d-DNNF compilation numbers must be produced on a Linux/x86 box (or CI). Everything
needed is already generated here; the Linux step is turnkey.

## What's already produced (machine-independent)

`python3 export_cnf.py` writes, for each provenance circuit:
- `cnf/<instance>.cnf` — a **weighted DIMACS CNF** (Tseitin encoding of the circuit's
  Boolean abstraction; `c p weight <lit> <w> 0` lines carry the token probabilities,
  gate/aux variables have weight 1).
- `cnf/manifest.json` — per instance: `expected_wmc` (verified == possible-world
  enumeration here) and `obdd_size` (our OBDD node count, the baseline to beat).

The CNF encoding is verified on this machine by an independent brute-force
weighted model count (`cnf_wmc_bruteforce`) equal to PWE — so d4's WMC on the same
CNF **must** reproduce `expected_wmc`.

## On a Linux/x86 box

```bash
# 1. build d4  (v1: crillab/d4, Makefile + GMP + bundled patoh)
git clone https://github.com/crillab/d4 && cd d4 && make        # -> ./d4
#    (or d4v2: git clone https://github.com/crillab/d4v2 && ./build.sh)

# 2. run the pipeline over the exported CNFs
cd provcircuit
D4=/path/to/d4 python3 d4_pipeline.py            # d4 v1
# D4=/path/to/d4v2 D4V2=1 python3 d4_pipeline.py  # d4 v2
```

`d4_pipeline.py` compiles each CNF to d-DNNF, records the **d-DNNF size**, then reads that dump and performs
the exact weighted count locally in one linear pass (it does **not** launch d4 a second time), and writes
`results.csv`. Each d4 compilation attempt uses the canonical **120 s** limit from
`experiment_timeouts.py`, identical to the OBDD cutoff. Two checks / outputs:

1. **Correctness:** the compiled-d-DNNF WMC equals `expected_wmc` for every instance (otherwise the CNF
   export, compiler output, NNF parser, or d4 flags are wrong). Expected values, e.g. drug/Omeprazole `0.774298`,
   drug/Clopidogrel `0.358800`.
2. **The figure:** compare `ddnnf_nodes` (d4) vs `obdd_size` (ours) per instance.
   This is where d-DNNF's `O(n·2^{O(tw)})` bound should beat OBDD's `n^{O(tw)}` on
   higher-treewidth / larger-`n` instances — the comparison experiment A showed
   PySDD cannot realize on M4.

## Scaling instances for the figure

To make the curve, regenerate CNFs for a family that grows (edit `export_cnf.py`'s
main to sweep, e.g., a bounded-treewidth family with growing `n`, and a
treewidth-varying family), then run `d4_pipeline.py`. Record `(n, tw, ddnnf_nodes,
obdd_size, d4_wmc)` and plot `ddnnf_nodes` and `obdd_size` vs `n` at each `tw`.

## d4 CLI note

d4 v1 and d4v2 differ in flags. `d4_pipeline.py` has both invocations; pin an unusual build with
`D4_DDNNF_CMD` / `D4V2_DDNNF_CMD` if needed. `ddnnf_wmc.py` accepts classic c2d NNF and d4's edge-labelled
NNF format; an unknown format fails loudly rather than falling back to a second counter.
