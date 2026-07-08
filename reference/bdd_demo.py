"""(1) Verify BDD compilation gives the same probabilities as enumeration & PWE
    on the engine-materialized circuits.
(2) Scale past enumeration: compile circuits with N leaves where 2^N worlds are
    infeasible, and check BDD-WMC against a closed form."""
import sys, time, rdflib
sys.setrecursionlimit(1_000_000)
import wmc, verify_all, compile_bdd, gates

C = rdflib.Namespace("urn:circuit:"); D = "urn:d:"
def short(x): return str(x).replace(D, "")

def to_circ(nt):
    typ, feeds, tin, minus, ans = verify_all.load(nt)
    circ = {}
    for t, ls in tin.items():
        for l in ls: circ[l] = ("leaf", short(l))
    for n, tp in typ.items():
        if tp == C.Times:  circ[n] = ("times", tuple(sorted(tin.get(n, ()))))
        elif tp == C.Plus: circ[n] = ("plus", tuple(sorted(feeds.get(n, ()))))
        elif tp == C.Minus: m = minus[n]; circ[n] = ("minus", (m["m"], m["s"]))
    ref = set()
    for op, pl in circ.values():
        if op in ("times", "plus"): ref |= set(pl)
        elif op == "minus": ref |= {pl[0], pl[1]}
    for r in ref:
        circ.setdefault(r, ("plus", ()))   # untyped referenced -> empty ⊕ = 0
    return circ, ans

print("=== (1) BDD-WMC vs enumeration vs PWE on engine circuits ===")
allok = True
for name in ["drug", "selfjoin", "minus", "optional"]:
    s = verify_all.REG[name]; circ, ans = to_circ(s["nt"]); P = s["P"]
    truth = {frozenset((v.lstrip("?"), val_) for (v, val_) in k): p
             for k, p in wmc.pwe(s["q"], s["sel"], s["base"], P).items()}
    print(f"[{name}]")
    for root, key in ans.items():
        pb, sz = compile_bdd.probability(circ, root, P)
        pe = compile_bdd.wmc_enum(circ, root, P)
        k = verify_all.parse_key(key)
        tp = truth.get(k, 0.0)
        ok = abs(pb - pe) < 1e-9 and abs(pb - tp) < 1e-9
        allok &= ok
        print(f"   {str(dict(k)):32} BDD={pb:.6f} enum={pe:.6f} PWE={tp:.6f} bdd_nodes={sz} {'OK' if ok else 'FAIL'}")

print("\n=== (2) scaling: shared-hub circuit  root = ⊕_i (t0 ⊗ t_i)  (t0 shared) ===")
def shared_hub(N):
    c = gates.Circuit()
    t0 = c.leaf("t0")
    root = c.plus([c.times([t0, c.leaf(f"t{i}")]) for i in range(1, N)])
    return c.gates, root

def closed(N, p):  # P = p(t0)·(1 - ∏(1-p(t_i)))
    return p * (1 - (1 - p) ** (N - 1))

print(f"{'N':>4} {'2^N worlds':>16} {'BDD nodes':>10} {'BDD-WMC':>12} {'closed form':>12} {'ms':>8}")
for N in [12, 20, 30, 40, 60, 100]:
    circ, root = shared_hub(N)
    P = {f"t{i}": 0.5 for i in range(N)}
    t = time.time()
    pb, sz = compile_bdd.probability(circ, root, P)
    ms = (time.time() - t) * 1000
    cf = closed(N, 0.5)
    tag = "OK" if abs(pb - cf) < 1e-9 else "FAIL"
    enum = f"{2**N:.2e}"
    # cross-check with enumeration only where feasible
    if N <= 20:
        pe = compile_bdd.wmc_enum(circ, root, P)
        tag += f" (enum={pe:.6f})"
    print(f"{N:>4} {enum:>16} {sz:>10} {pb:>12.6f} {cf:>12.6f} {ms:>8.1f}  {tag}")

print("\nALL OK" if allok else "\nFAILURES")
