# Level-1 d4v2 head-to-head + d4-v2 verify — STATUS: build-blocked (author-gated)

## d4-v2 verify — MOOT (resolved by other means; no d4-v2 needed)

The checklist's d4-v2 tasks exist to fix d4-v1's weighted-count over-count on large reconvergent path
CNFs. **That concern is now resolved without d4-v2:**
- **`PathIsoSeq` (`7882a1e`)** collapses WD-path cones to **≤ 20 tokens** — there are **no > 40-token path
  cones left**, so "d4-v2 on the large path cones" is moot (checklist explicitly said to check this first).
- **`ddnnf_wmc.py`** computes the weighted count over d4's `-dDNNF` dump **ourselves** (linear pass),
  bypassing d4-v1's buggy `-mc`. `g6_d4_real.py` now shows **d4 (local WMC) == OBDD == PWE, 26/26**
  including all 16 property paths (commit `f846a29`).
- E4's synthetic high-treewidth families remain the genuine d4/order-robustness case and are **already
  correct with d4-v1** (`watdiv/e4_results.csv`) — the checklist marks E4 core "do not re-run".

So d4-v2 buys nothing for the real workloads here; d4-v1 + local d-DNNF WMC is sufficient and verified.

## Level-1 per-answer head-to-head (`level1_d4_headtohead.py`) — BLOCKED on the d4v2 build

The Level-1 harness (per-answer CNF → **one pinned d4v2** on both ours and ProvSQL) requires a built
`d4v2` binary and a ProvSQL `d4v2-cnf` tool registered to that same binary. **d4v2 does not build here:**

Attempted (all applied): `conda install zlib ninja`; `-DCMAKE_POLICY_VERSION_MINIMUM=3.5` on d4v2 +
bipe CMakeLists (their `cmake_minimum_required` is 3.1, rejected by modern CMake); conda `CPATH`/`LIBRARY_PATH`.
Result: flowCutter, glucose-3.0, bipe, and `libd4.a` compile, **but the final link fails** on two missing
partitioner libraries the repo does not ship:
- **`3rdParty/patoh/libpatoh.a`** — only the PaToH **binary** (`patoh`) + `patoh.h` are shipped, not the
  static lib. PaToH is proprietary (Bilkent); `libpatoh.a` must be obtained from its distribution site.
- **`3rdParty/kahypar/`** — **absent entirely** (the `CMakeLists.txt` links `kahypar/build/lib/libkahypar.a`),
  so KaHyPar would have to be cloned and built separately (its own CMake project + deps).

Dropping both would require editing d4v2's CMake **and** its `PartitionerPatoh.cpp` / `PartitionerKahypar`
sources — invasive and risky. So Level-1 is **author-gated**: it needs either a prebuilt `d4v2` binary, or
`libpatoh.a` + a KaHyPar checkout, dropped into `tools/d4v2/3rdParty/`.

## What already covers the intent

- **Exact-probability parity with ProvSQL is established** without d4v2: R8.3 (`r8_3_reconvergent.py`)
  shows ours == ProvSQL `probability_evaluate()` per `c_custkey` (`max_abs_error = 0.0`) on a reconvergent
  query, plus ours == the closed form.
- **d4/OBDD/PWE agreement** is established by G6 (26/26).
- The Level-1 *framing* (control the external compiler per-answer on both sides) is the remaining
  author-gated item; it does not change any correctness conclusion.
