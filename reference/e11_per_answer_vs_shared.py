"""E11 - per-answer knowledge compilation (NPCS / SPARQLprov how-provenance) vs our shared circuit.

NPCS and SPARQLprov emit how-provenance PER ANSWER (a polynomial / string, with shared subterms
repeated) and do NOT compute probabilities. This experiment *completes* them into a PQE pipeline by
compiling each answer's how-provenance with OUR knowledge compiler (compile_bdd), so both sides use the
SAME compiler and the ONLY difference is the representation:

    theirs : one provenance polynomial PER ANSWER, compiled independently (no cross-answer sharing)
    ours   : ONE shared content-addressed circuit over all answers, compiled once

Held constant: the query, data, leaf probabilities, variable order, and the compiler. The only variable
is shared-circuit compile (one ROBDD, cross-answer node sharing) vs per-answer compile (a fresh ROBDD per
answer). So any difference is attributable to SHARING, not the compiler -- the answer to "is your win
from the shared circuit or from your compiler?". This is the sequel to E2 (which compares representation
SIZE): it shows that difference translates into PQE COST.

Sides:
  - OURS   : the FACTORED shared circuit (the E2/E5 construction), compiled once into a shared ROBDD.
  - THEIRS : the NPCS/SPARQLprov per-answer how-provenance = the flat sum-of-products STRING (derivations
             spelled out, no factoring), each answer compiled independently.

TWO WINS (both measured):
  - REPRESENTATION (repr_win = T_string / T_circuit): our factored circuit is far more compact than the
    per-answer strings (E2, up to 201x, unbounded on deep/recurring families). Order-independent.
  - COMPILE-TIME AT SCALE (scale_sweep): with N answers SHARING a sub-provenance of compiled size S, one
    shared pass builds it ONCE = Theta(N+S), while per-answer rebuilds it per answer = Theta(N*S). The
    absolute saving grows with N (measured: ~9x time at N=1000), the ratio bounded by S. This is the
    "1000 answers -> we compile once, they compile 1000 times" intuition, and it holds when answers share
    provenance (the common case, and the premise of a shared circuit).

  CAVEAT (to stay honest): the compile-time win needs (a) actual cross-answer sharing -- fully independent
  answers give N disjoint functions with nothing to reuse; and (b) for an OBDD, a variable order that lets
  the shared sub-BDD merge -- a shared PREFIX at the TOP of the order does NOT merge, which is exactly why
  Result 1's per-instance table (built with a DFS shared-as-prefix order) shows compiled_win ~= 1.0x.
  d-DNNF/d4 picks its own decomposition and is more robust to order. So Result 1 is the WORST-CASE
  (no-reuse) order; scale_sweep uses a sharing-friendly order to expose the reuse the shared circuit enables.

Why this is faithful to the baselines: our engine reproduces NPCS's rewriting (BGP-verified identical),
so an answer's cone in our circuit IS the per-answer how-provenance NPCS/SPARQLprov compute for it, and
its sum-of-products expansion is their emitted string. For SPARQLprov's MINUS we use its ACTUAL semantics
(unguarded DIFF = gamma guard=False, read from its released rewriter): the last section shows compiling
SPARQLprov's how-provenance yields the WRONG probability on a disjoint MINUS while ours matches PWE.

Pure Python, zero dependencies. Run: `python3 e11_per_answer_vs_shared.py`.
"""
import sys, time, csv
sys.setrecursionlimit(1_000_000)
import gates, gamma, wmc, compile_bdd, factor


# ------------------------------- families (same as E2 / bench.py) -----------------------------------
def layered(k, W):
    data = {}
    for a in range(W): data[f"e0_{a}"] = ("S", "p", f"n1_{a}")
    for i in range(1, k):
        for a in range(W):
            for b in range(W): data[f"e{i}_{a}_{b}"] = (f"n{i}_{a}", "p", f"n{i+1}_{b}")
    pats = [("S", "p", "?v1")] + [(f"?v{i}", "p", f"?v{i+1}") for i in range(1, k)]
    return data, ("bgp", pats), [f"?v{k}"]

DRUG = {"p1": ("Aspirin", "iw", "Warfarin"), "p2": ("Warfarin", "iw", "Metformin"),
        "p3": ("Metformin", "iw", "Omeprazole"), "p4": ("Aspirin", "iw", "Ibuprofen"),
        "p5": ("Ibuprofen", "iw", "Metformin"), "p6": ("Warfarin", "iw", "Lisinopril"),
        "p7": ("Lisinopril", "iw", "Clopidogrel"), "p8": ("Clopidogrel", "iw", "Aspirin")}
DRUG_Q = ("bgp", [("Aspirin", "iw", "?x"), ("?x", "iw", "?y"), ("?y", "iw", "?z")])
DRUG_SEL = ["?z"]

def shared_prefix(d, N):
    """A depth-d chain shared by ALL answers, fanning out to N answers -- maximal CROSS-answer sharing.
    Per-answer compilation redoes the shared chain N times; our shared circuit compiles it once. Low
    treewidth (a chain), so the OBDD stays small: the sharing win is visible AND the compile is feasible."""
    data = {}
    for i in range(d): data[f"c{i}"] = (f"h{i}", "p", f"h{i+1}")     # chain h0 -p-> ... -p-> hd
    for j in range(N): data[f"b{j}"] = (f"h{d}", "q", f"ans{j}")     # hd -q-> ans0..ans{N-1}
    pats = [("h0", "p", "?x1")] + [(f"?x{i}", "p", f"?x{i+1}") for i in range(1, d)] + [(f"?x{d}", "q", "?ans")]
    return data, ("bgp", pats), ["?ans"]


# ------------------------------- circuit / compile helpers ------------------------------------------
def global_order(circ, roots):
    """One variable order (leaf tokens, DFS first-appearance across ALL answer roots) -- used for BOTH
    shared and per-answer compilation so the size difference is purely node sharing, not order choice."""
    order, seen, inord = [], set(), set()
    def dfs(n):
        if n in seen: return
        seen.add(n); op, pl = circ[n]
        if op == "leaf":
            if pl not in inord: inord.add(pl); order.append(pl)   # set membership: O(N), identical order
        elif op in ("times", "plus"):
            for c in pl: dfs(c)
        elif op == "minus":
            dfs(pl[0]); dfs(pl[1])
    for r in roots.values(): dfs(r)
    return order

def repr_size(circ, roots):
    """Representation size of the shared circuit (gates + edges of the DAG reachable from all answers) --
    the T_circuit of E2. Contrast T_string = Σ_answers Σ_derivations arity (the per-answer string)."""
    seen = set(); edges = 0; st = list(roots.values())
    while st:
        n = st.pop()
        if n in seen: continue
        seen.add(n); op, pl = circ[n]
        kids = pl if op in ("plus", "times") else ((pl[0], pl[1]) if op == "minus" else ())
        edges += len(kids); st += list(kids)
    return len(seen) + edges

def _reachable(bdd, node_ids):
    seen = set(); st = list(node_ids)
    while st:
        n = st.pop()
        if n <= 1 or n in seen: continue
        seen.add(n); _, lo, hi = bdd.nodes[n]; st += [lo, hi]
    return len(seen)

def compile_shared(circ, roots, P, order):
    """OURS: compile every answer root into ONE ROBDD (shared unique-table + memo) -> cross-answer
    node sharing. Returns (compiled_size, compile_ms, {answer: prob})."""
    bdd = compile_bdd.ROBDD(order); memo = {}; nodes = {}
    t = time.time()
    for key, r in roots.items():
        nodes[key] = compile_bdd.compile_root(circ, r, bdd, memo)
    ms = (time.time() - t) * 1000
    size = _reachable(bdd, nodes.values())
    return size, ms, {key: bdd.wmc(n, P) for key, n in nodes.items()}

def cone(circ, root):
    """The sub-circuit reachable from an answer root = that answer's how-provenance (shared DAG)."""
    sub = {}; st = [root]
    while st:
        n = st.pop()
        if n in sub: continue
        op, pl = circ[n]; sub[n] = (op, pl)
        if op in ("times", "plus"): st += list(pl)
        elif op == "minus": st += [pl[0], pl[1]]
    return sub

def flatten_sop(circ, root):
    """Expand a monotone cone to a flat sum-of-products circuit -- the NPCS/SPARQLprov emitted STRING
    (derivations spelled out; leaves still shared, but products are not)."""
    def monomials(n):
        op, pl = circ[n]
        if op == "leaf": return [frozenset([pl])]
        if op == "const": return [frozenset()] if pl else []
        if op == "times":
            combos = [frozenset()]
            for c in pl: combos = [a | b for a in combos for b in monomials(c)]
            return combos
        if op == "plus":
            out = []
            for c in pl: out += monomials(c)
            return out
        raise ValueError("SoP undefined for non-monotone (minus)")
    fc = {}
    def L(t): fc[("L", t)] = ("leaf", t); return ("L", t)
    prods = []
    for i, m in enumerate(monomials(root)):
        fc[("T", i)] = ("times", tuple(sorted(L(t) for t in m)))
        prods.append(("T", i))
    fc[("A",)] = ("plus", tuple(prods))
    return fc, ("A",)

def compile_per_answer(circ, roots, P, order, flat):
    """THEIRS: compile each answer's how-provenance in a FRESH ROBDD (no cross-answer sharing).
    flat=False -> STEELMAN (cone DAG); flat=True -> FAITHFUL (flat sum-of-products string)."""
    size = 0; ms = 0.0; probs = {}
    for key, r in roots.items():
        sub, rr = (flatten_sop(circ, r) if flat else (cone(circ, r), r))
        t = time.time()
        bdd = compile_bdd.ROBDD(order)
        node = compile_bdd.compile_root(sub, rr, bdd, {})
        ms += (time.time() - t) * 1000
        size += bdd.size(node)
        probs[key] = bdd.wmc(node, P)
    return size, ms, probs


# ------------------------------- monotone families ---------------------------------------------------
COLS = ["instance", "answers", "derivations", "tokens", "T_string", "T_circuit", "repr_win",
        "size_shared", "size_perans", "compiled_win", "t_ours_ms", "t_theirs_ms",
        "theirs_form", "prob_parity", "pwe_maxdiff"]
FLAT_CAP = 256        # above this #derivations the flat SoP is infeasible; use the cone (steelman) proxy

def run(name, q, data, sel):
    assert q[0] == "bgp", "E11 monotone families are BGPs"
    pats, P = q[1], {t: 0.5 for t in data}
    nderiv, ntok, arity = len(wmc._plain_eval(q, set(data.values()))), len(data), len(pats)

    # OURS: the FACTORED shared circuit (E2/E5), compiled once into a shared ROBDD.
    cf = gates.Circuit(); roots_ours = factor.factored_bgp(cf, pats, data, set(sel))
    order = global_order(cf.gates, roots_ours)                       # token order shared by both sides
    T_circuit = repr_size(cf.gates, roots_ours)
    s_size, s_ms, s_prob = compile_shared(cf.gates, roots_ours, P, order)

    # THEIRS: NPCS/SPARQLprov per-answer how-provenance = flat sum-of-products, compiled per answer.
    cflat = gates.Circuit(); roots_theirs = gamma.project(cflat, gamma.eval_q(cflat, q, data), sel)
    flat = nderiv <= FLAT_CAP
    pa_size, pa_ms, pa_prob = compile_per_answer(cflat.gates, roots_theirs, P, order, flat=flat)

    keys = set(s_prob) & set(pa_prob)
    parity = max((abs(s_prob[k] - pa_prob[k]) for k in keys), default=0.0)   # ours == theirs (both exact)
    if ntok <= 20:
        truth = wmc.pwe(q, sel, data, P)
        pwe = max(abs(s_prob.get(k, 0.0) - truth.get(k, 0.0)) for k in set(s_prob) | set(truth))
        pwe_s = f"{pwe:.1e}"
    else:
        pwe_s = f"n/a(2^{ntok})"
    T_string = nderiv * arity
    repr_win = round(T_string / T_circuit, 1) if T_circuit else 1.0
    compiled_win = round(pa_size / s_size, 2) if s_size else 1.0     # ~1.0: compilation is per-answer
    row = dict(instance=name, answers=len(roots_ours), derivations=nderiv, tokens=ntok,
               T_string=T_string, T_circuit=T_circuit, repr_win=repr_win,
               size_shared=s_size, size_perans=pa_size, compiled_win=compiled_win,
               t_ours_ms=round(s_ms, 1), t_theirs_ms=round(pa_ms, 1),
               theirs_form=("flat-SoP" if flat else "cone(steelman)"),
               prob_parity=f"{parity:.1e}", pwe_maxdiff=pwe_s)
    print(f"{name:13} ans={len(roots_ours):>3} deriv={nderiv:>4} | REPR T_str={T_string:>6} "
          f"T_circ={T_circuit:>5} ({repr_win:>5.1f}x win) | COMPILED ours={s_size:>6} theirs={pa_size:>6} "
          f"({compiled_win:>4.2f}x) | parity={parity:.0e} pwe={pwe_s}")
    return row


# ------------------------------- MINUS: SPARQLprov's how-provenance is WRONG -------------------------
def scale_sweep(d=8, Ns=(50, 200, 500, 1000)):
    """The cross-answer COMPILE-TIME win at scale (the reviewer's / user's intuition, measured).
    N answers share a depth-d sub-provenance, compiled with a SHARING-FRIENDLY order (selectors on top,
    the shared chain at the bottom, so its sub-BDD merges). Per-answer recompiles the shared chain N
    times = Theta(N*S); the shared pass builds it once = Theta(N+S). The absolute saving grows with N;
    the ratio is bounded by the shared size S. (With a shared-as-PREFIX order -- Result 1's global_order
    -- the sub-BDD cannot merge and the win vanishes: for OBDD the win is order-realizable; d-DNNF/d4,
    which picks its own decomposition, is more robust.)"""
    print(f"\n=== compile-time win at scale: N answers sharing a depth-{d} sub-provenance (good order) ===")
    print(f"{'N':>5} {'shared_size':>11} {'perans_size':>11} {'size_win':>8} "
          f"{'shared_ms':>9} {'perans_ms':>10} {'time_win':>8}")
    rows = []
    for N in Ns:
        data, q, sel = shared_prefix(d, N)
        P = {t: 0.5 for t in data}
        c = gates.Circuit(); roots = gamma.project(c, gamma.eval_q(c, q, data), sel)
        order = [f"b{j}" for j in range(N)] + [f"c{i}" for i in range(d)]     # sharing-friendly
        ss, st, _ = compile_shared(c.gates, roots, P, order)
        ps, pt, _ = compile_per_answer(c.gates, roots, P, order, flat=False)
        print(f"{N:>5} {ss:>11} {ps:>11} {ps/ss:>7.1f}x {st:>9.1f} {pt:>10.1f} {pt/max(st,1e-9):>7.1f}x")
        rows.append(dict(N=N, d=d, shared_size=ss, perans_size=ps, size_win=round(ps / ss, 1),
                         shared_ms=round(st, 1), perans_ms=round(pt, 1),
                         time_win=round(pt / max(st, 1e-9), 1)))
    return rows


def minus_bug():
    """Disjoint-operand MINUS (P1={?x likes ?y}, P2={?z owns ?w} share NO variable). Compile each
    system's per-answer how-provenance and WMC it: ours (guarded, W3C) matches PWE; SPARQLprov's
    (unguarded DIFF) over-subtracts -> wrong probability."""
    data = {"t1": ("A", "likes", "X"), "t2": ("B", "likes", "Y"), "u1": ("C", "owns", "Z")}
    P = {t: 0.5 for t in data}
    qa, qb, sel = ("bgp", [("?x", "likes", "?y")]), ("bgp", [("?z", "owns", "?w")]), ["?x", "?y"]
    q = ("minus", qa, qb)
    truth = wmc.pwe(q, sel, data, P)

    def pqe(guard):
        c = gates.Circuit()
        roots = gamma.project(c, gamma.eval_minus(c, qa, qb, data, guard=guard), sel)
        return {k: compile_bdd.probability(c.gates, r, P)[0] for k, r in roots.items()}

    ours, sprov = pqe(True), pqe(False)                       # guard=True = W3C/NPCS; False = SPARQLprov
    print("\n=== MINUS (disjoint operands): compiling each system's how-provenance -> probability ===")
    print(f"{'answer':22} {'ours(guarded)':>14} {'SPARQLprov(unguarded)':>22} {'PWE(truth)':>12}  verdict")
    rows = []
    for k in sorted(truth, key=lambda s: sorted(dict(s).items())):
        a = dict(k); tp = truth[k]; op = ours.get(k, 0.0); sp = sprov.get(k, 0.0)
        ok = abs(op - tp) < 1e-9; bad = abs(sp - tp) >= 1e-9
        verdict = "ours OK; SPARQLprov WRONG" if (ok and bad) else ("all match" if ok and not bad else "??")
        lab = ",".join(f"{v}={a[v]}" for v in sel if v in a)
        print(f"{lab:22} {op:>14.4f} {sp:>22.4f} {tp:>12.4f}  {verdict}")
        rows.append(dict(answer=lab, ours_guarded=round(op, 4), sparqlprov_unguarded=round(sp, 4),
                         pwe_truth=round(tp, 4), ours_matches_pwe=ok, sparqlprov_wrong=bad))
    return rows


if __name__ == "__main__":
    print("=== E11: per-answer knowledge compilation (NPCS/SPARQLprov) vs shared circuit (ours) ===")
    print("(same query, data, probabilities, variable order, and compiler -- only sharing differs)\n")
    # The compile-feasible regime (as E2/E4): bounded-treewidth families whose OBDD does not blow up.
    # For high-width instances (layered-4x6/8) the OBDD is ~10^4-10^5 nodes (E4) and BOTH sides are
    # #P-hard to compile -- the representation-size win there is E2 (T_string vs T_circuit, up to 201x).
    rows = [run("drug", DRUG_Q, DRUG, DRUG_SEL)]
    print("--- shared-prefix: d-chain shared by N answers (isolates CROSS-answer sharing) ---")
    for d, N in [(4, 4), (4, 8), (4, 16), (8, 8), (8, 16)]:
        dat, q, o = shared_prefix(d, N); rows.append(run(f"prefix-d{d}xN{N}", q, dat, o))
    print("--- layered k=4, growing width (multi-answer, low treewidth) ---")
    for W in [2, 3, 4]:
        dat, q, o = layered(4, W); rows.append(run(f"layered-4x{W}", q, dat, o))
    # deeper (deep-12x2) has T_string/T_circuit = 201x (E2) but its naive-order OBDD blows up (E4
    # caveat), so the compiled comparison there is #P-bound on both sides; deep-8x2 shows the pattern.
    print("--- width-2, growing depth (deep within-answer sharing -> large repr win, ~1x compiled) ---")
    for k in [4, 8]:
        dat, q, o = layered(k, 2); rows.append(run(f"deep-{k}x2", q, dat, o))
    with open("e11_results.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS); w.writeheader(); w.writerows(rows)
    srows = scale_sweep()
    with open("e11_scale.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(srows[0].keys())); w.writeheader(); w.writerows(srows)
    mrows = minus_bug()
    with open("e11_minus.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(mrows[0].keys())); w.writeheader(); w.writerows(mrows)
    print("\nwrote e11_results.csv, e11_scale.csv, e11_minus.csv")
