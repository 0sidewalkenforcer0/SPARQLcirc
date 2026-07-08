"""Verify the ENGINE-MATERIALIZED circuit (built by the emitted CONSTRUCT via
CircuitRun) computes correct probabilities: WMC over it == possible-world
enumeration over the drug KG + 3-hop query."""
import rdflib, itertools

C = rdflib.Namespace("urn:circuit:")
RDFT = rdflib.RDF.type

g = rdflib.Graph().parse("data/drug.circuit.nt", format="nt")

# --- rebuild gate structure from the materialized RDF ---
plus, times, feeds, ins, answer = set(), set(), {}, {}, {}
for s, p, o in g:
    if p == RDFT and o == C.Plus:  plus.add(s)
    if p == RDFT and o == C.Times: times.add(s)
    if p == C.feeds:  feeds.setdefault(s, set()).add(o)   # times -> its plus
    if p == C["in"]:  ins.setdefault(s, set()).add(o)     # times -> its leaf tokens
    if p == C.answer: answer[s] = str(o)

plus_children = {pl: {t for t in times if pl in feeds.get(t, ())} for pl in plus}

def short(u): return str(u).replace("urn:d:", "")
leaves = sorted({short(l) for t in times for l in ins.get(t, ())})

# --- WMC over the circuit (Boolean abstraction) ---
def wmc(plus_gate, P):
    total = 0.0
    for bits in itertools.product((0, 1), repeat=len(leaves)):
        asn = dict(zip(leaves, bits))
        # plus = OR over its times children; times = AND over its leaves
        sat = any(all(asn[short(l)] for l in ins[t]) for t in plus_children[plus_gate])
        if sat:
            w = 1.0
            for l in leaves:
                w *= P[l] if asn[l] else 1 - P[l]
            total += w
    return total

# --- drug KG + probabilities (Fig. 1) ---
EDGES = {"p1":("Aspirin","Warfarin",0.92),"p2":("Warfarin","Metformin",0.87),
         "p3":("Metformin","Omeprazole",0.85),"p4":("Aspirin","Ibuprofen",0.78),
         "p5":("Ibuprofen","Metformin",0.71),"p6":("Warfarin","Lisinopril",0.65),
         "p7":("Lisinopril","Clopidogrel",0.60),"p8":("Clopidogrel","Aspirin",0.55)}
P = {k: pr for k, (_, _, pr) in EDGES.items()}

def pwe():
    toks = list(EDGES); res = {}
    for bits in itertools.product((0, 1), repeat=len(toks)):
        active = {t for t, b in zip(toks, bits) if b}
        edges = [(EDGES[t][0], EDGES[t][1]) for t in active]
        # 3-hop from Aspirin: Aspirin->x->y->z
        succ = {}
        for a, b in edges: succ.setdefault(a, set()).add(b)
        zs = set()
        for x in succ.get("Aspirin", ()):
            for y in succ.get(x, ()):
                zs |= succ.get(y, set())
        w = 1.0
        for t, b in zip(toks, bits): w *= P[t] if b else 1 - P[t]
        for z in zs: res[z] = res.get(z, 0.0) + w
    return res

truth = pwe()
print(f"leaves in circuit: {leaves}")
print(f"shared leaves (in >=2 Times gates): "
      f"{sorted(short(l) for l in {l for l in {x for t in times for x in ins.get(t,())}} if sum(1 for t in times if l in ins.get(t,()))>=2)}")
print("\nanswer            P(circuit-WMC)   P(PWE)")
ok = True
for pl, key in sorted(answer.items(), key=lambda kv: kv[1]):
    z = key.split("|")[-1].split("=", 1)[-1].replace("urn:d:", "")   # "A|z=urn:d:Omeprazole" -> "Omeprazole"
    cp, tp = wmc(pl, P), truth.get(z, 0.0)
    flag = "OK" if abs(cp - tp) < 1e-9 else "MISMATCH"
    if flag != "OK": ok = False
    print(f"  {z:12}    {cp:.6f}       {tp:.6f}   {flag}")
print("\nALL MATCH" if ok else "\nFAILURES")
