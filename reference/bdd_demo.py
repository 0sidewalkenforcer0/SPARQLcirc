"""(1) Verify BDD compilation gives the same probabilities as enumeration & PWE
    on the engine-materialized circuits.
(2) Scale past enumeration: compile circuits with N leaves where 2^N worlds are
    infeasible, and check BDD-WMC against a closed form."""
import sys, time
sys.setrecursionlimit(1_000_000)
import wmc, verify_all, compile_bdd, gates, circuit_io

def _truth(s):   # term-aware PWE keys (matching circuit_io.answer_key over c:binding)
    P = s["P"]; out = {}
    for k, p in wmc.pwe(s["q"], s["sel"], s["base"], P).items():
        d = dict(k)
        out[circuit_io.answer_key({sv.lstrip("?"): (circuit_io.canon_iri("urn:d:" + d[sv]) if sv in d else "u")
                                   for sv in s["sel"]})] = p
    return out

print("=== (1) BDD-WMC vs enumeration vs PWE on engine circuits ===")
allok = True
for name in ["drug", "selfjoin", "minus", "optional"]:
    s = verify_all.REG[name]
    circ, answers, bindings = circuit_io.parse(open(s["nt"]).read())
    Pf = {"urn:d:" + k: v for k, v in s["P"].items()}                 # circuit leaves are urn:d: token IRIs
    truth = _truth(s)
    print(f"[{name}]")
    for g in answers:
        key = circuit_io.answer_key(bindings[g])
        pb, sz = compile_bdd.probability(circ, g, Pf)
        pe = compile_bdd.wmc_enum(circ, g, Pf)
        tp = truth.get(key, 0.0)
        ok = abs(pb - pe) < 1e-9 and abs(pb - tp) < 1e-9
        allok &= ok
        print(f"   {key:34} BDD={pb:.6f} enum={pe:.6f} PWE={tp:.6f} bdd_nodes={sz} {'OK' if ok else 'FAIL'}")

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
