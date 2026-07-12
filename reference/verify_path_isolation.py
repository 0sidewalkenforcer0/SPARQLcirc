"""Property-path STATE-ISOLATION regression (the P1 fix).

Two DIFFERENT property-path queries that share a (from,to) base pair but use DIFFERENT predicates must
NOT compose with / collapse onto each other's reach/base gates when they run against the SAME writable
store. Before the fix every reach/base gate was keyed only by (level, from, to) and matched by `c:rlvl`
alone, so on a shared endpoint query B would pull in query A's persisted base gates (SAME gate IRI,
DIFFERENT token) -> contaminated provenance (a node reachable via :p OR :q instead of only the queried
predicate).

The fix threads a deterministic per-path FINGERPRINT into (a) every reach/base gate IRI and (b) a
`c:rpath` guard on every step/seed/project match pattern. This test builds a graph with parallel edges
:p and :q over the SAME node pairs, runs `:A :p+ ?y` and `:A :q+ ?y`, UNIONS both emitted circuits
(simulating one shared store), and asserts:
  1. the two paths have DIFFERENT, single-valued fingerprints;
  2. their base-gate IRIs are DISJOINT           (pre-fix: identical -> the bug);
  3. in the union, NO base gate is fed by more than one distinct token (no cross-path merge);
  4. re-running a query is byte-identical         (fingerprint determinism / idempotence).
"""
import subprocess, os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
JAR  = os.path.join(HERE, "..", "engine", "target", "npcs-rewrite.jar")
C    = "urn:circuit:"
TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"

DATA = """@prefix : <http://example.org/paper#> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
# Parallel edges over the SAME pairs (A,B) and (B,C): :p (tokens e1,e2) and :q (tokens e3,e4).
# Same (from,to), different predicate -> pre-fix the base gates H("base",from,to) COLLIDE across queries.
:e1 rdf:subject :A ; rdf:predicate :p ; rdf:object :B .
:e2 rdf:subject :B ; rdf:predicate :p ; rdf:object :C .
:e3 rdf:subject :A ; rdf:predicate :q ; rdf:object :B .
:e4 rdf:subject :B ; rdf:predicate :q ; rdf:object :C .
"""
Q = lambda pred: f"PREFIX : <http://example.org/paper#>\nSELECT ?y WHERE {{ :A :{pred}+ ?y }}\n"


def run(data_path, query_path):
    return subprocess.run(["java", "-cp", JAR, "npcs.circuit.CircuitRun", "Standard", data_path, query_path],
                          capture_output=True, text=True, check=True).stdout


def parse(nt):
    """-> (typ, rlvl, rpath, feeds, tin): feeds[o]={s..} (s c:feeds o); tin[s]={o..} (s c:in o)."""
    typ, rlvl, rpath, feeds, tin = {}, {}, {}, {}, {}
    for line in nt.splitlines():
        line = line.strip()
        if not line.endswith(" ."):
            continue
        s, p, o = line[:-2].split(None, 2)
        s, p, o = s.strip("<>"), p.strip("<>"), o.strip()
        oi = o.strip("<>")
        if p == TYPE:            typ[s] = oi
        elif p == C + "rlvl":    rlvl[s] = o.strip('"')
        elif p == C + "rpath":   rpath[s] = o.strip('"')
        elif p == C + "feeds":   feeds.setdefault(oi, set()).add(s)
        elif p == C + "in":      tin.setdefault(s, set()).add(oi)
    return typ, rlvl, rpath, feeds, tin


def base_gate_tokens(typ, rlvl, feeds, tin):
    """{base_gate_iri: set(distinct leaf tokens feeding it through its Times children)}."""
    out = {}
    for g, lvl in rlvl.items():
        if lvl != "base":
            continue
        toks = set()
        for f in feeds.get(g, ()):                       # the Times gates feeding this base Plus
            for x in tin.get(f, ()):                     # their c:in children
                if x not in typ:                         # a leaf token (reification node), not a gate
                    toks.add(x)
        out[g] = toks
    return out


def main():
    if not os.path.exists(JAR):
        print("jar not built:", JAR); sys.exit(2)
    d = tempfile.mkdtemp(prefix="pathiso_")
    data = os.path.join(d, "parallel.ttl"); open(data, "w").write(DATA)
    qp = os.path.join(d, "p.sparql"); open(qp, "w").write(Q("p"))
    qq = os.path.join(d, "q.sparql"); open(qq, "w").write(Q("q"))

    nt_p, nt_q = run(data, qp), run(data, qq)
    tp, lp, rp, fp_, ip = parse(nt_p)
    tq, lq, rq, fq, iq = parse(nt_q)

    ok = True
    # (1) each query has ONE fingerprint, and the two differ.
    fps_p, fps_q = set(rp.values()), set(rq.values())
    c1 = len(fps_p) == 1 and len(fps_q) == 1 and fps_p != fps_q
    ok &= c1
    print(f"[1] single, distinct path fingerprints        {'OK' if c1 else 'FAIL'}  "
          f"(:p+ ={next(iter(fps_p))[:12]}…, :q+ ={next(iter(fps_q))[:12]}…)")

    # (2) base-gate IRIs are disjoint across the two queries (pre-fix they were identical).
    base_p = {g for g, v in lp.items() if v == "base"}
    base_q = {g for g, v in lq.items() if v == "base"}
    shared = base_p & base_q
    c2 = base_p and base_q and not shared
    ok &= c2
    print(f"[2] base-gate IRIs disjoint across queries     {'OK' if c2 else 'FAIL'}  "
          f"(:p+ {len(base_p)} base, :q+ {len(base_q)} base, shared={len(shared)})")

    # (3) UNION both circuits (one shared store) — no base gate fed by >1 distinct token.
    def merge(a, b):
        m = {k: set(v) for k, v in a.items()}
        for k, v in b.items():
            m.setdefault(k, set()).update(v)
        return m
    typ = {**tp, **tq}; rlvl = {**lp, **lq}
    feeds = merge(fp_, fq); tin = merge(ip, iq)              # fp_/fq = feeds maps, ip/iq = c:in maps
    bt = base_gate_tokens(typ, rlvl, feeds, tin)
    multi = {g: t for g, t in bt.items() if len(t) > 1}
    c3 = bt and not multi
    ok &= c3
    print(f"[3] union: no base gate fed by >1 token        {'OK' if c3 else 'FAIL'}  "
          f"({len(bt)} base gates, {len(multi)} contaminated)")
    if multi:
        for g, t in list(multi.items())[:4]:
            print(f"      CONTAMINATED {g}  <- {sorted(x.rsplit('#',1)[-1] for x in t)}")

    # (4) determinism: re-run :p+ -> byte-identical (sorted) triples and identical fingerprint.
    nt_p2 = run(data, qp)
    c4 = sorted(nt_p.splitlines()) == sorted(nt_p2.splitlines())
    ok &= c4
    print(f"[4] re-run byte-identical (deterministic fp)   {'OK' if c4 else 'FAIL'}")

    # (5) REAL same-endpoint sequential test via the Java PathIsoSeq harness: run the second path query
    #     BOTH after the first on ONE shared store (gates fed back, NOT cleaned) AND alone, and require
    #     the circuits to be identical. Covers :p* -> :p+ specifically (star must be in the fingerprint,
    #     else :p*'s persisted zero-length reach gates leak into :p+). This is the persistent-endpoint
    #     scenario the two-fresh-stores union check in (1)-(3) does NOT exercise.
    G = os.path.join(HERE, "..", "engine", "examples", "gallery")
    seq_ok = True
    for first, second in (("pathstar.sparql", "pathplus.sparql"), ("pathplus.sparql", "pathstar.sparql")):
        r = subprocess.run(["java", "-cp", JAR, "npcs.circuit.PathIsoSeq",
                            os.path.join(G, "pathcyc.ttl"), os.path.join(G, first), os.path.join(G, second)],
                           capture_output=True, text=True)
        good = r.returncode == 0
        seq_ok &= good
        print(f"[5] shared store: {first.split('.')[0]:9} then {second.split('.')[0]:9} -> "
              f"{'OK (second query uncontaminated)' if good else 'FAIL (contamination)'}")
    ok &= seq_ok

    print("\nALL OK" if ok else "\nFAILURES")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
