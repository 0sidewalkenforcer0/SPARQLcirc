"""Consolidated verification of the engine-materialized circuits:
  (1) correctness: WMC over the RDF circuit == possible-world enumeration (PWE);
  (2) ⊗ canonicalization: no two Times gates share the same child multiset
      (i.e. content addressing is order-independent -> maximal sharing, and the
      multiset-hash collision concern is closed)."""
import sys, itertools, rdflib
import wmc

C = rdflib.Namespace("urn:circuit:")
RDFT = rdflib.RDF.type
D = "urn:d:"

def short(x): return str(x).replace(D, "")

REG = {
  "drug": dict(nt="data/drug.circuit.nt", sel=["?z"],
     base={"p1":("Aspirin","iw","Warfarin"),"p2":("Warfarin","iw","Metformin"),
           "p3":("Metformin","iw","Omeprazole"),"p4":("Aspirin","iw","Ibuprofen"),
           "p5":("Ibuprofen","iw","Metformin"),"p6":("Warfarin","iw","Lisinopril"),
           "p7":("Lisinopril","iw","Clopidogrel"),"p8":("Clopidogrel","iw","Aspirin")},
     P={f"p{i}":v for i,v in zip(range(1,9),[.92,.87,.85,.78,.71,.65,.60,.55])},
     q=("bgp",[("Aspirin","iw","?x"),("?x","iw","?y"),("?y","iw","?z")])),
  "selfjoin": dict(nt="data/selfjoin.circuit.nt", sel=["?d"],
     base={"s1":("A","p","B"),"s2":("C","p","B")}, P={"s1":.5,"s2":.5},
     q=("bgp",[("?x","p","?d"),("?y","p","?d")])),
  "minus": dict(nt="data/minus.circuit.nt", sel=["?x"],
     base={"u1":("Alice","likes","pasta"),"u2":("Alice","likes","pasta"),
           "u3":("Alice","livesIn","Italy"),"u4":("Bob","likes","pasta")},
     P={"u1":.5,"u2":.3,"u3":.7,"u4":.6},
     q=("minus",("bgp",[("?x","likes","pasta")]),("bgp",[("?x","livesIn","Italy")]))),
  "optional": dict(nt="data/optional.circuit.nt", sel=["?x","?c"],
     base={"u1":("Alice","likes","pasta"),"u2":("Alice","likes","pasta"),
           "u3":("Alice","livesIn","Italy"),"u4":("Bob","likes","pasta")},
     P={"u1":.5,"u2":.3,"u3":.7,"u4":.6},
     q=("optional",("bgp",[("?x","likes","pasta")]),("bgp",[("?x","livesIn","?c")]))),
}

def load(nt):
    g = rdflib.Graph().parse(nt, format="nt")
    typ, feeds, tin, minus, ans = {}, {}, {}, {}, {}
    for s,p,o in g:
        if   p==RDFT:          typ[s]=o
        elif p==C.feeds:       feeds.setdefault(o,set()).add(s)
        elif p==C["in"]:       tin.setdefault(s,set()).add(o)
        elif p==C.minuend:     minus.setdefault(s,{})["m"]=o
        elif p==C.subtrahend:  minus.setdefault(s,{})["s"]=o
        elif p==C.answer:      ans[s]=str(o)
    return typ, feeds, tin, minus, ans

def val(n, typ, feeds, tin, minus, asn, memo):
    if n in memo: return memo[n]
    t = typ.get(n)
    if   t==C.Times: r = all(asn[short(l)] for l in tin.get(n,()))
    elif t==C.Plus:  r = any(val(c,typ,feeds,tin,minus,asn,memo) for c in feeds.get(n,()))
    elif t==C.Minus: m=minus[n]; r = val(m["m"],typ,feeds,tin,minus,asn,memo) and not val(m["s"],typ,feeds,tin,minus,asn,memo)
    else:            r = asn[short(n)]
    memo[n]=r; return r

def parse_key(k):
    out=[]
    for part in k.split("|")[1:]:
        var,_,v = part.partition("=")
        if v!="NULL": out.append((var, short(v)))
    return frozenset(out)

def run(name):
    s = REG[name]
    typ, feeds, tin, minus, ans = load(s["nt"])
    toks = list(s["base"]); P = s["P"]
    # (1) correctness
    circ={}
    for root,key in ans.items():
        tot=0.0
        for bits in itertools.product((0,1),repeat=len(toks)):
            asn=dict(zip(toks,bits))
            if val(root,typ,feeds,tin,minus,asn,{}):
                w=1.0
                for t in toks: w*=P[t] if asn[t] else 1-P[t]
                tot+=w
        circ[parse_key(key)]=tot
    truth={frozenset((v.lstrip("?"),val_) for (v,val_) in k):p
           for k,p in wmc.pwe(s["q"],s["sel"],s["base"],P).items()}
    keys=set(circ)|{k for k,v in truth.items() if v>1e-12}
    ok=all(abs(circ.get(k,0.0)-truth.get(k,0.0))<1e-9 for k in keys)
    # (2) ⊗ canonicalization: group Times gates by their child-set; each group must be size 1
    times=[n for n,t in typ.items() if t==C.Times]
    sig={}
    for tg in times: sig.setdefault(frozenset(short(l) for l in tin.get(tg,())), []).append(tg)
    dupes={k:v for k,v in sig.items() if len(v)>1}
    canon = not dupes
    print(f"[{name}] correctness={'OK' if ok else 'FAIL'}  "
          f"Times-gates={len(times)} distinct-child-multisets={len(sig)} "
          f"canonical(no congruent ⊗)={'YES' if canon else 'NO'}")
    if not ok:
        for k in sorted(keys,key=str):
            if abs(circ.get(k,0.0)-truth.get(k,0.0))>=1e-9:
                print(f"    MISMATCH {dict(k)} circuit={circ.get(k,0.0):.6f} PWE={truth.get(k,0.0):.6f}")
    if dupes:
        for cs,gs in dupes.items(): print(f"    CONGRUENT ⊗ not merged: children={set(cs)} -> {len(gs)} gates")
    return ok and canon

if __name__=="__main__":
    names = sys.argv[1:] or ["drug","selfjoin","minus","optional"]
    allok = all(run(n) for n in names)
    print("\nALL OK" if allok else "\nFAILURES")
    sys.exit(0 if allok else 1)
