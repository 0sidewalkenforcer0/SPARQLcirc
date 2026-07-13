"""Consolidated verification of the engine-materialized circuits:
  (1) correctness: WMC over the RDF circuit == possible-world enumeration (PWE);
  (2) fixture structure: expected Times-gate counts, including the self-join
      derivation-order sharing regression.

The serialized RDF child set cannot prove general multiset-key uniqueness: [x]
and [x,x] legitimately have different pre-serialization keys but the same RDF
edge set. Java source-level regressions cover key construction itself."""
import sys
import wmc, compile_bdd, circuit_io

REG = {
  "drug": dict(nt="data/drug.circuit.nt", sel=["?z"],
     times=3,
     base={"p1":("Aspirin","iw","Warfarin"),"p2":("Warfarin","iw","Metformin"),
           "p3":("Metformin","iw","Omeprazole"),"p4":("Aspirin","iw","Ibuprofen"),
           "p5":("Ibuprofen","iw","Metformin"),"p6":("Warfarin","iw","Lisinopril"),
           "p7":("Lisinopril","iw","Clopidogrel"),"p8":("Clopidogrel","iw","Aspirin")},
     P={f"p{i}":v for i,v in zip(range(1,9),[.92,.87,.85,.78,.71,.65,.60,.55])},
     q=("bgp",[("Aspirin","iw","?x"),("?x","iw","?y"),("?y","iw","?z")])),
  "selfjoin": dict(nt="data/selfjoin.circuit.nt", sel=["?d"],
     times=3,
     base={"s1":("A","p","B"),"s2":("C","p","B")}, P={"s1":.5,"s2":.5},
     q=("bgp",[("?x","p","?d"),("?y","p","?d")])),
  "minus": dict(nt="data/minus.circuit.nt", sel=["?x"],
     times=4,
     base={"u1":("Alice","likes","pasta"),"u2":("Alice","likes","pasta"),
           "u3":("Alice","livesIn","Italy"),"u4":("Bob","likes","pasta")},
     P={"u1":.5,"u2":.3,"u3":.7,"u4":.6},
     q=("minus",("bgp",[("?x","likes","pasta")]),("bgp",[("?x","livesIn","Italy")]))),
  "optional": dict(nt="data/optional.circuit.nt", sel=["?x","?c"],
     times=6,
     base={"u1":("Alice","likes","pasta"),"u2":("Alice","likes","pasta"),
           "u3":("Alice","livesIn","Italy"),"u4":("Bob","likes","pasta")},
     P={"u1":.5,"u2":.3,"u3":.7,"u4":.6},
     q=("optional",("bgp",[("?x","likes","pasta")]),("bgp",[("?x","livesIn","?c")]))),
}

def run(name):
    s = REG[name]
    circ, answers, bindings = circuit_io.parse(open(s["nt"]).read())    # shared TERM-AWARE parser
    P = s["P"]
    Pf = {"urn:d:" + k: v for k, v in P.items()}                        # circuit leaves are the urn:d: token IRIs
    # (1) correctness: possible-world enumeration over the circuit == PWE, keyed by c:binding (not the string)
    cw = {circuit_io.answer_key(bindings[g]): round(compile_bdd.wmc_enum(circ, g, Pf), 10) for g in answers}
    truth = {}
    for k, p in wmc.pwe(s["q"], s["sel"], s["base"], P).items():
        if p > 1e-12:
            d = dict(k)                                                 # base values are IRIs under urn:d:
            truth[circuit_io.answer_key({sv.lstrip("?"): (circuit_io.canon_iri("urn:d:" + d[sv]) if sv in d else "u")
                                         for sv in s["sel"]})] = round(p, 10)
    keys = set(cw) | set(truth)
    ok = all(abs(cw.get(k, 0.0) - truth.get(k, 0.0)) < 1e-9 for k in keys)
    # (2) Stable fixture-level gate count. In particular selfjoin=3 (not 4)
    # checks that derivation order does not manufacture a second product gate.
    times = [n for n, (op, _) in circ.items() if op == "times"]
    sig = {}
    for tg in times: sig.setdefault(frozenset(circ[tg][1]), []).append(tg)
    boolean_aliases = sum(len(v) - 1 for v in sig.values())
    structure = len(times) == s["times"]
    print(f"[{name}] correctness={'OK' if ok else 'FAIL'}  "
          f"Times-gates={len(times)}/{s['times']} structure={'OK' if structure else 'FAIL'} "
          f"Boolean-child-set-aliases={boolean_aliases}")
    if not ok:
        for k in sorted(keys):
            if abs(cw.get(k, 0.0) - truth.get(k, 0.0)) >= 1e-9:
                print(f"    MISMATCH {k} circuit={cw.get(k,0.0):.6f} PWE={truth.get(k,0.0):.6f}")
    return ok and structure

if __name__=="__main__":
    names = sys.argv[1:] or ["drug","selfjoin","minus","optional"]
    allok = all(run(n) for n in names)
    print("\nALL OK" if allok else "\nFAILURES")
    sys.exit(0 if allok else 1)
