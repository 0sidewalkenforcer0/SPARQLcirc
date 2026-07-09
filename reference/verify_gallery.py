"""Engine-side regression test for EVERY minimal operator.

For atom (BGP) / join (⊗) / union (⊕) / minus (⊖) / optional, build the circuit
with the emitted CONSTRUCT (via CircuitRun on a tiny reified KG), evaluate WMC
over the materialized circuit (Boolean abstraction: Times=∧, Plus=∨,
Minus(a,b)=a∧¬b), and check it equals possible-world enumeration over the
operator's set semantics. This guards, in particular, against UNION being
mis-compiled to a join (which a BGP-only test cannot catch)."""
import subprocess, itertools, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
JAR = os.path.join(HERE, "..", "engine", "target", "npcs-rewrite.jar")
G = os.path.join(HERE, "..", "engine", "examples", "gallery")
RS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
EX = "http://example.org/paper#"

# the reified KG (must match engine/examples/gallery/gallery.ttl), token -> (s,p,o)
TRIP = {"t1": ("Alice", "knows", "Bob"),  "t2": ("Alice", "knows", "Carol"),
        "t3": ("Bob", "knows", "Carol"),  "t4": ("Alice", "likes", "Bob"),
        "t5": ("Alice", "blocks", "Carol"), "t6": ("Bob", "city", "Rome")}
TOKS = list(TRIP)
P = {"t1": .9, "t2": .8, "t3": .7, "t4": .6, "t5": .5, "t6": .4}

# ---- build the circuit on the unmodified in-memory engine, parse the RDF ----
def circuit(op):
    nt = subprocess.run(["java", "-cp", JAR, "npcs.circuit.CircuitRun", "Standard",
                         f"{G}/gallery.ttl", f"{G}/{op}.sparql"],
                        capture_output=True, text=True, check=True).stdout
    kind, feeds, ins, mnd, sub, answer = {}, {}, {}, {}, {}, {}
    for line in nt.splitlines():
        if not line.endswith(" ."): continue
        s, p, o = line[:-2].split(None, 2)
        s, p = s.strip("<>"), p.strip("<>"); oi = o.strip().strip("<>")
        if p == RS + "type": kind[s] = oi.split(":")[-1]              # Plus/Times/Minus
        elif p == "urn:circuit:feeds": feeds.setdefault(s, set()).add(oi)
        elif p == "urn:circuit:in": ins.setdefault(s, set()).add(oi.replace(EX, ""))
        elif p == "urn:circuit:minuend": mnd[s] = oi
        elif p == "urn:circuit:subtrahend": sub[s] = oi
        elif p == "urn:circuit:answer": answer[s] = o.strip().strip('"')
    feeders = {}
    for g, tgts in feeds.items():
        for t in tgts: feeders.setdefault(t, set()).add(g)
    return kind, feeders, ins, mnd, sub, answer

def evalg(g, asn, kind, feeders, ins, mnd, sub):
    k = kind.get(g)
    if k == "Times": return all(asn[t] for t in ins.get(g, ()))
    if k == "Minus": return evalg(mnd[g], asn, kind, feeders, ins, mnd, sub) and \
                            not evalg(sub[g], asn, kind, feeders, ins, mnd, sub)
    if k == "Plus":  return any(evalg(f, asn, kind, feeders, ins, mnd, sub)
                                for f in feeders.get(g, ()))
    return False

def circuit_wmc(op):
    kind, feeders, ins, mnd, sub, answer = circuit(op)
    out = {}
    for gate, key in answer.items():
        tot = 0.0
        for bits in itertools.product((0, 1), repeat=len(TOKS)):
            asn = dict(zip(TOKS, bits))
            if evalg(gate, asn, kind, feeders, ins, mnd, sub):
                w = 1.0
                for t in TOKS: w *= P[t] if asn[t] else 1 - P[t]
                tot += w
        out[key] = round(tot, 10)
    return out

# ---- ground truth: possible-world enumeration over the set semantics ----
def answers(op, T):   # T = set of (s,p,o) triples that hold in this world
    kn = {(s, o) for (s, pr, o) in T if pr == "knows"}
    def key(**kv): return "A|" + "|".join(f"{k}={v}" for k, v in kv.items())
    if op == "atom":
        return {key(y=EX + o) for (s, o) in kn if s == "Alice"}
    if op == "join":
        return {key(z=EX + z) for (a, y) in kn if a == "Alice" for (b, z) in kn if b == y}
    if op == "union":
        lk = {(s, o) for (s, pr, o) in T if pr == "likes"}
        return {key(y=EX + o) for (s, o) in kn | lk if s == "Alice"}
    if op == "minus":
        bl = {(s, o) for (s, pr, o) in T if pr == "blocks"}
        return {key(y=EX + o) for (s, o) in kn if s == "Alice" and ("Alice", o) not in bl}
    if op == "minus_disjoint":                 # MINUS, no shared variable => no-op => P1
        return {key(y=EX + o) for (s, o) in kn if s == "Alice"}
    if op == "minus_union":                    # (knows ∪ likes) MINUS blocks  (UNION left operand)
        lk = {(s, o) for (s, pr, o) in T if pr == "likes"}
        bl = {(s, o) for (s, pr, o) in T if pr == "blocks"}
        p1 = {o for (s, o) in kn | lk if s == "Alice"}
        return {key(y=EX + o) for o in p1 if ("Alice", o) not in bl}
    if op == "minus_p2union":                  # knows MINUS ({?y :city ?c} UNION {?w :blocks ?y})
        citysubj = {s for (s, pr, o) in T if pr == "city"}     # ?y :city ?c  -> y is subject
        blockedobj = {o for (s, pr, o) in T if pr == "blocks"} # ?w :blocks ?y -> y is object
        return {key(y=EX + o) for (s, o) in kn
                if s == "Alice" and o not in citysubj and o not in blockedobj}
    if op == "optional":
        city = {(s, o) for (s, pr, o) in T if pr == "city"}
        out = set()
        for (s, y) in kn:
            if s != "Alice": continue
            cs = [c for (yy, c) in city if yy == y]
            if cs: out |= {key(y=EX + y, c=EX + c) for c in cs}
            else:  out.add(key(y=EX + y, c="NULL"))
        return out
    raise ValueError(op)

def pwe(op):
    res = {}
    for bits in itertools.product((0, 1), repeat=len(TOKS)):
        active = [t for t, b in zip(TOKS, bits) if b]
        T = {TRIP[t] for t in active}
        w = 1.0
        for t in TOKS: w *= P[t] if t in active else 1 - P[t]
        for a in answers(op, T): res[a] = res.get(a, 0.0) + w
    return {k: round(v, 10) for k, v in res.items()}

# ---- guard: out-of-fragment queries must be REJECTED, not silently mis-compiled ----
def rejects(cmd):
    """True iff the tool fails loudly (non-zero exit + 'Unsupported' in its output)."""
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode != 0 and "Unsupported" in (r.stderr + r.stdout)

def check_guard():
    fq = f"{G}/filter_unsupported.sparql"
    npcs = rejects(["java", "-jar", JAR, "Standard", "path", fq])                    # NpcsRewriter
    circ = rejects(["java", "-cp", JAR, "npcs.circuit.CircuitRun", "Standard",
                    f"{G}/gallery.ttl", fq])                                          # CircuitRewriter
    print(f"[guard   ] FILTER rejected — NpcsRewriter? {'OK' if npcs else 'FAIL'}   "
          f"CircuitRewriter? {'OK' if circ else 'FAIL'}")
    return npcs and circ

if __name__ == "__main__":
    allok = True
    for op in ["atom", "join", "union", "minus", "minus_disjoint", "minus_union", "minus_p2union", "optional"]:
        cw, tw = circuit_wmc(op), pwe(op)
        keys = sorted(set(cw) | set(tw))
        ok = all(abs(cw.get(k, 0.0) - tw.get(k, 0.0)) < 1e-9 for k in keys)
        allok &= ok
        print(f"[{op:8}] answers={len(tw):2}  circuit==PWE? {'OK' if ok else 'MISMATCH'}")
        for k in keys:
            flag = "" if abs(cw.get(k, 0.) - tw.get(k, 0.)) < 1e-9 else "   <-- MISMATCH"
            print(f"    {k:<48} circuit={cw.get(k,0.):.6f}  pwe={tw.get(k,0.):.6f}{flag}")
    allok &= check_guard()
    print("\nALL OK" if allok else "\nFAILURES")
    sys.exit(0 if allok else 1)
