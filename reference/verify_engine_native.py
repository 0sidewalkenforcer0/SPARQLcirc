"""Verify the ENGINE-MATERIALIZED circuit (built by the emitted CONSTRUCT via
CircuitRun) computes correct probabilities: WMC over it == possible-world
enumeration over the drug KG + 3-hop query. Answers are recovered TERM-AWARE from
the structured c:binding nodes (via circuit_io), not the lossy c:answer string."""
import itertools, os, sys
import compile_bdd, circuit_io

HERE = os.path.dirname(os.path.abspath(__file__))
circ, answers, bindings = circuit_io.parse(open(os.path.join(HERE, "data/drug.circuit.nt")).read())

# --- drug KG + probabilities (Fig. 1) ---
EDGES = {"p1": ("Aspirin", "Warfarin", 0.92), "p2": ("Warfarin", "Metformin", 0.87),
         "p3": ("Metformin", "Omeprazole", 0.85), "p4": ("Aspirin", "Ibuprofen", 0.78),
         "p5": ("Ibuprofen", "Metformin", 0.71), "p6": ("Warfarin", "Lisinopril", 0.65),
         "p7": ("Lisinopril", "Clopidogrel", 0.60), "p8": ("Clopidogrel", "Aspirin", 0.55)}
P = {k: pr for k, (_, _, pr) in EDGES.items()}
Pf = {"urn:d:" + k: v for k, v in P.items()}                          # circuit leaves are urn:d: token IRIs

def pwe():
    toks = list(EDGES); res = {}
    for bits in itertools.product((0, 1), repeat=len(toks)):
        active = {t for t, b in zip(toks, bits) if b}
        edges = [(EDGES[t][0], EDGES[t][1]) for t in active]
        succ = {}
        for a, b in edges: succ.setdefault(a, set()).add(b)
        zs = set()
        for x in succ.get("Aspirin", ()):                            # 3-hop Aspirin->x->y->z
            for y in succ.get(x, ()):
                zs |= succ.get(y, set())
        w = 1.0
        for t, b in zip(toks, bits): w *= P[t] if b else 1 - P[t]
        for z in zs: res[z] = res.get(z, 0.0) + w
    return res

truth = pwe()
print("answer            P(circuit-WMC)   P(PWE)")
ok = True
for g in answers:
    z = bindings[g].get("z", "").split("\x1f")[-1].replace("urn:d:", "")   # c:binding term -> bare drug name
    cp, tp = compile_bdd.wmc_enum(circ, g, Pf), truth.get(z, 0.0)
    flag = "OK" if abs(cp - tp) < 1e-9 else "MISMATCH"
    if flag != "OK": ok = False
    print(f"  {z:12}    {cp:.6f}       {tp:.6f}   {flag}")
print("\nALL MATCH" if ok else "\nFAILURES")
sys.exit(0 if ok else 1)
