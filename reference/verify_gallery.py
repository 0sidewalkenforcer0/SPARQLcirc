"""Engine-side regression test for EVERY minimal operator.

For atom (BGP) / join (⊗) / union (⊕) / minus (⊖) / optional, build the circuit
with the emitted CONSTRUCT (via CircuitRun on a tiny reified KG), evaluate WMC
over the materialized DAG (Boolean abstraction: Times=∧, Plus=∨,
Minus(a,b)=a∧¬b), and check it equals possible-world enumeration over the
operator's set semantics.  The production invocation deliberately uses
CircuitRun's default factored construction; a separate explicit ``flat`` run is
kept as the ablation regression.  This guards, in particular, against UNION
being mis-compiled to a join (which a BGP-only test cannot catch), and against a
harness which only understands the old flat token→Times→Plus shape.

Answer identity is TERM-AWARE: an answer gate is recovered from its structured
`c:binding`/`c:var`/`c:val` nodes (which preserve IRI vs literal, datatype, lang,
bound/unbound), NOT from the lossy readable `c:answer` label. The PWE oracle keys
answers the same way, so two answers that differ only by term type never merge on
either side (that would hide the STR-collision bug; see verify_answer_keys.py)."""
import subprocess, itertools, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
JAR = os.path.join(HERE, "..", "engine", "target", "npcs-rewrite.jar")
G = os.path.join(HERE, "..", "engine", "examples", "gallery")
RS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
EX = "http://example.org/paper#"
XSD_STRING = "http://www.w3.org/2001/XMLSchema#string"
RDF_LANGSTRING = RS + "langString"

# the reified KG (must match engine/examples/gallery/gallery.ttl), token -> (s,p,o)
TRIP = {"t1": ("Alice", "knows", "Bob"),  "t2": ("Alice", "knows", "Carol"),
        "t3": ("Bob", "knows", "Carol"),  "t4": ("Alice", "likes", "Bob"),
        "t5": ("Alice", "blocks", "Carol"), "t6": ("Bob", "city", "Rome")}
TOKS = list(TRIP)
P = {"t1": .9, "t2": .8, "t3": .7, "t4": .6, "t5": .5, "t6": .4}

# ---- term-aware canonicalization (identical on circuit + oracle sides) ----
def tcanon(kind, lex, dt, lang):
    if kind == "u":    return "u"                        # unbound
    if kind == "iri":  return "i\x1f" + lex
    if kind == "bnode": return "b\x1f" + lex
    return "l\x1f" + lex + "\x1f" + (dt or XSD_STRING) + "\x1f" + (lang or "")

def canon_ntoken(tok):
    """Canonicalize a raw N-Triples object token (from c:val)."""
    if tok is None: return "u"
    tok = tok.strip()
    if tok.startswith("<") and tok.endswith(">"): return tcanon("iri", tok[1:-1], None, None)
    if tok.startswith("_:"): return tcanon("bnode", tok[2:], None, None)
    if tok.startswith('"'):
        i = tok.rindex('"'); lex, suf = tok[1:i], tok[i + 1:]
        if suf.startswith("^^<") and suf.endswith(">"): return tcanon("lit", lex, suf[3:-1], None)
        if suf.startswith("@"): return tcanon("lit", lex, RDF_LANGSTRING, suf[1:].lower())
        return tcanon("lit", lex, XSD_STRING, None)
    return "?\x1f" + tok

def canon_rdflib(term):
    import rdflib
    if term is None: return "u"
    if isinstance(term, rdflib.URIRef): return tcanon("iri", str(term), None, None)
    if isinstance(term, rdflib.BNode):  return tcanon("bnode", str(term), None, None)
    lang = (term.language or "").lower()
    dt = str(term.datatype) if term.datatype else (RDF_LANGSTRING if lang else XSD_STRING)
    return tcanon("lit", str(term), dt, lang)

def anskey(pairs):
    """Order-independent term-aware answer key from {var: canonical-value}."""
    return "A|" + "|".join(sorted(f"{v}={c}" for v, c in pairs.items())) if pairs else "A"

# ---- build the circuit on the unmodified in-memory engine, parse the RDF ----
def circuit(op, construction=None):
    """Return the parsed engine circuit.

    ``construction=None`` is intentional: it exercises CircuitRun's production
    default.  Tests may pass ``"flat"`` only for the explicit ablation.
    """
    cmd = ["java", "-cp", JAR, "npcs.circuit.CircuitRun"]
    if construction is not None:
        if construction not in {"factored", "flat"}:
            raise ValueError(f"unknown construction mode: {construction}")
        cmd.append(f"--construction={construction}")
    cmd += ["Standard", f"{G}/gallery.ttl", f"{G}/{op}.sparql"]
    nt = subprocess.run(cmd,
                        capture_output=True, text=True, check=True).stdout
    kind, feeds, ins, mnd, sub = {}, {}, {}, {}, {}
    ans_gates, g_bnodes, b_var, b_val = set(), {}, {}, {}
    for line in nt.splitlines():
        if not line.endswith(" ."): continue
        s, p, o = line[:-2].split(None, 2)
        s, p, o = s.strip("<>"), p.strip("<>"), o.strip()
        oi = o.strip("<>")
        if p == RS + "type": kind[s] = oi.split(":")[-1]              # Plus/Times/Minus
        elif p == "urn:circuit:feeds": feeds.setdefault(s, set()).add(oi)
        elif p == "urn:circuit:in": ins.setdefault(s, set()).add(oi.replace(EX, ""))
        elif p == "urn:circuit:minuend": mnd[s] = oi
        elif p == "urn:circuit:subtrahend": sub[s] = oi
        elif p == "urn:circuit:answer": ans_gates.add(s)              # readable label only -> mark as answer
        elif p == "urn:circuit:binding": g_bnodes.setdefault(s, set()).add(oi)
        elif p == "urn:circuit:var": b_var[s] = o.strip('"')
        elif p == "urn:circuit:val": b_val[s] = o                    # RAW N-Triples term token
    feeders = {}
    for g, tgts in feeds.items():
        for t in tgts: feeders.setdefault(t, set()).add(g)
    binds = {}                                                        # gate -> {var: canonical value}
    for g in ans_gates:
        d = {}
        for b in g_bnodes.get(g, ()):
            v = b_var.get(b)
            if v is not None: d[v] = canon_ntoken(b_val.get(b))       # missing c:val -> "u" (unbound)
        binds[g] = d
    return kind, feeders, ins, mnd, sub, ans_gates, binds

def evalg(g, asn, kind, feeders, ins, mnd, sub, memo=None, active=None):
    """Evaluate one node of an arbitrary nested circuit DAG.

    Both c:in and c:feeds edges may point to another gate *or* directly to a
    probabilistic token.  The latter is the base-Plus shape emitted by factored
    construction.  ``memo`` preserves DAG sharing; ``active`` turns an invalid
    cycle into a useful test failure rather than unbounded recursion.
    """
    if memo is None: memo = {}
    if active is None: active = set()
    if g in memo: return memo[g]

    k = kind.get(g)
    if k is None:
        # c:in historically stored gallery tokens as the local t1 form, while
        # c:feeds retains the full token IRI.  Accept either without treating an
        # unknown RDF node as Boolean false (which used to hide nested-DAG bugs).
        candidates = (g, g[len(EX):]) if g.startswith(EX) else (g,)
        for token in candidates:
            if token in asn:
                memo[g] = bool(asn[token])
                return memo[g]
        raise ValueError(f"circuit leaf {g!r} has no probability assignment")

    if g in active:
        raise ValueError(f"cycle in materialized circuit at {g!r}")
    active.add(g)
    try:
        if k == "Times":
            value = all(evalg(child, asn, kind, feeders, ins, mnd, sub, memo, active)
                        for child in ins.get(g, ()))
        elif k == "Plus":
            value = any(evalg(child, asn, kind, feeders, ins, mnd, sub, memo, active)
                        for child in feeders.get(g, ()))
        elif k == "Minus":
            if g not in mnd or g not in sub:
                raise ValueError(f"Minus gate {g!r} is missing an operand")
            value = (evalg(mnd[g], asn, kind, feeders, ins, mnd, sub, memo, active)
                     and not evalg(sub[g], asn, kind, feeders, ins, mnd, sub,
                                   memo, active))
        else:
            raise ValueError(f"unknown circuit gate type {k!r} at {g!r}")
    finally:
        active.remove(g)
    memo[g] = value
    return value

def circuit_wmc(op, construction=None):
    kind, feeders, ins, mnd, sub, ans_gates, binds = circuit(op, construction)
    out = {}
    for gate in ans_gates:                                            # key by TERM-AWARE binding, not the string
        tot = 0.0
        for bits in itertools.product((0, 1), repeat=len(TOKS)):
            asn = dict(zip(TOKS, bits))
            if evalg(gate, asn, kind, feeders, ins, mnd, sub):
                w = 1.0
                for t in TOKS: w *= P[t] if asn[t] else 1 - P[t]
                tot += w
        out[anskey(binds.get(gate, {}))] = round(tot, 10)
    return out

def check_nested_evaluator():
    """Truth-table regression for base-Plus→Times→Plus→Minus→answer.

    This tiny synthetic DAG isolates the evaluator contract from Java query
    generation.  The engine-output checks below separately prove that the same
    nested shapes really occur on the default production path.
    """
    b1, b2, b3 = "urn:test:b1", "urn:test:b2", "urn:test:b3"
    product, left, minus, answer = ("urn:test:product", "urn:test:left",
                                    "urn:test:minus", "urn:test:answer")
    kind = {b1: "Plus", b2: "Plus", b3: "Plus", product: "Times",
            left: "Plus", minus: "Minus", answer: "Plus"}
    feeders = {
        b1: {EX + "t1"}, b2: {EX + "t2"}, b3: {EX + "t3"},
        left: {product}, answer: {minus},
    }
    ins = {product: {b1, b2}}
    mnd, sub = {minus: left}, {minus: b3}
    ok = True
    for bits in itertools.product((0, 1), repeat=3):
        asn = dict(zip(("t1", "t2", "t3"), bits))
        got = evalg(answer, asn, kind, feeders, ins, mnd, sub)
        want = bool(asn["t1"] and asn["t2"] and not asn["t3"])
        ok &= got == want
    print(f"[nested ] base-Plus/Times/Minus evaluator: {'OK' if ok else 'FAIL'}")
    return ok

def check_construction_modes():
    """Lock down default factored structure and the explicit flat ablation."""
    jk, jf, ji, _, _, _, _ = circuit("join")       # no flag: production default
    base_pluses = {g for g, k in jk.items() if k == "Plus"
                   and any(source not in jk for source in jf.get(g, ()))}
    nested_products = {g for g, k in jk.items() if k == "Times"
                       and any(child in base_pluses for child in ji.get(g, ()))}
    factored_shape = bool(base_pluses and nested_products)

    mk, mf, _, mm, ms, ma, _ = circuit("minus")     # default requests factored; operator falls back
    minus_gates = {g for g, k in mk.items() if k == "Minus"}
    nested_minus = bool(minus_gates) and all(
        mk.get(mm.get(g)) == "Plus" and mk.get(ms.get(g)) == "Plus"
        for g in minus_gates
    ) and any(any(source in minus_gates for source in mf.get(answer, ())) for answer in ma)

    flat_ok = True
    for op in ("atom", "join"):
        flat = circuit_wmc(op, "flat")
        truth = pwe(op)
        keys = set(flat) | set(truth)
        flat_ok &= all(abs(flat.get(k, 0.0) - truth.get(k, 0.0)) < 1e-9 for k in keys)
    fk, ff, _, _, _, _, _ = circuit("join", "flat")
    flat_has_base_plus = any(k == "Plus" and any(source not in fk for source in ff.get(g, ()))
                             for g, k in fk.items())
    flat_shape = not flat_has_base_plus

    print(f"[default ] factored base-Plus -> Times nesting: {'OK' if factored_shape else 'FAIL'}")
    print(f"[default ] nested Plus -> Minus -> answer: {'OK' if nested_minus else 'FAIL'}")
    print(f"[flat    ] explicit BGP ablation WMC==PWE/old shape: "
          f"{'OK' if flat_ok and flat_shape else 'FAIL'}")
    return factored_shape and nested_minus and flat_ok and flat_shape

# ---- ground truth: possible-world enumeration over the set semantics ----
def answers_rdflib(op, T):
    """Oracle for arbitrary queries: evaluate the actual .sparql on world T with rdflib (its own W3C
    MINUS/OPTIONAL semantics), keyed TERM-AWARE (canon_rdflib) to match the circuit's c:binding."""
    import rdflib
    g = rdflib.Graph()
    for (s, p, o) in T:
        g.add((rdflib.URIRef(EX + s), rdflib.URIRef(EX + p), rdflib.URIRef(EX + o)))
    res = g.query(open(f"{G}/{op}.sparql").read())
    pvars = [str(v) for v in res.vars]
    out = set()
    for row in res:
        out.add(anskey({v: canon_rdflib(row[rdflib.Variable(v)]) for v in pvars}))
    return out

RDFLIB_OPS = {"opt_left", "opt_right", "minus_chain", "distinct", "opt_disjoint"}  # complex: oracle via rdflib

def answers(op, T):   # T = set of (s,p,o) triples that hold in this world
    if op in RDFLIB_OPS:
        return answers_rdflib(op, T)
    kn = {(s, o) for (s, pr, o) in T if pr == "knows"}
    I = lambda v: tcanon("iri", EX + v, None, None)                   # gallery values are all IRIs
    if op == "atom":
        return {anskey({"y": I(o)}) for (s, o) in kn if s == "Alice"}
    if op == "join":
        return {anskey({"z": I(z)}) for (a, y) in kn if a == "Alice" for (b, z) in kn if b == y}
    if op == "union":
        lk = {(s, o) for (s, pr, o) in T if pr == "likes"}
        return {anskey({"y": I(o)}) for (s, o) in kn | lk if s == "Alice"}
    if op == "minus":
        bl = {(s, o) for (s, pr, o) in T if pr == "blocks"}
        return {anskey({"y": I(o)}) for (s, o) in kn if s == "Alice" and ("Alice", o) not in bl}
    if op == "minus_disjoint":                 # MINUS, no shared variable => no-op => P1
        return {anskey({"y": I(o)}) for (s, o) in kn if s == "Alice"}
    if op == "minus_union":                    # (knows ∪ likes) MINUS blocks
        lk = {(s, o) for (s, pr, o) in T if pr == "likes"}
        bl = {(s, o) for (s, pr, o) in T if pr == "blocks"}
        p1 = {o for (s, o) in kn | lk if s == "Alice"}
        return {anskey({"y": I(o)}) for o in p1 if ("Alice", o) not in bl}
    if op == "minus_p2union":                  # knows MINUS ({?y :city ?c} UNION {?w :blocks ?y})
        citysubj = {s for (s, pr, o) in T if pr == "city"}
        blockedobj = {o for (s, pr, o) in T if pr == "blocks"}
        return {anskey({"y": I(o)}) for (s, o) in kn
                if s == "Alice" and o not in citysubj and o not in blockedobj}
    if op == "optional":
        city = {(s, o) for (s, pr, o) in T if pr == "city"}
        out = set()
        for (s, y) in kn:
            if s != "Alice": continue
            cs = [c for (yy, c) in city if yy == y]
            if cs: out |= {anskey({"y": I(y), "c": I(c)}) for c in cs}
            else:  out.add(anskey({"y": I(y), "c": "u"}))            # ?c unbound -> "u", not literal "NULL"
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
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode != 0 and "Unsupported" in (r.stderr + r.stdout)

def check_guard():
    def circ(f): return rejects(["java", "-cp", JAR, "npcs.circuit.CircuitRun", "Standard",
                                 f"{G}/gallery.ttl", f"{G}/{f}"])
    def npcs(f): return rejects(["java", "-jar", JAR, "Standard", "path", f"{G}/{f}"])
    r = {"FILTER (NpcsRewriter)":        npcs("filter_unsupported.sparql"),
         "FILTER (CircuitRewriter)":     circ("filter_unsupported.sparql"),
         "LIMIT (CircuitRewriter)":      circ("limit.sparql"),
         "right-nested MINUS (Circuit)": circ("minus_rnested.sparql"),
         "x-product OPT-in-MINUS (Circ)": circ("opt_xprod.sparql")}
    for name, ok in r.items():
        print(f"[reject  ] {name}: {'OK' if ok else 'FAIL'}")
    return all(r.values())

if __name__ == "__main__":
    allok = check_nested_evaluator()
    for op in ["atom", "join", "union", "minus", "minus_disjoint", "minus_union", "minus_p2union",
               "minus_chain", "opt_left", "opt_right", "distinct", "optional", "opt_disjoint"]:
        cw, tw = circuit_wmc(op), pwe(op)
        keys = sorted(set(cw) | set(tw))
        ok = all(abs(cw.get(k, 0.0) - tw.get(k, 0.0)) < 1e-9 for k in keys)
        allok &= ok
        print(f"[{op:8}] answers={len(tw):2}  circuit==PWE? {'OK' if ok else 'MISMATCH'}")
        for k in keys:
            flag = "" if abs(cw.get(k, 0.) - tw.get(k, 0.)) < 1e-9 else "   <-- MISMATCH"
            print(f"    {k:<52} circuit={cw.get(k,0.):.6f}  pwe={tw.get(k,0.):.6f}{flag}")
    allok &= check_construction_modes()
    allok &= check_guard()
    print("\nALL OK" if allok else "\nFAILURES")
    sys.exit(0 if allok else 1)
