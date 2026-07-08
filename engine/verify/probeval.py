#!/usr/bin/env python3
"""How to do PROBABILISTIC query evaluation on top of NPCS provenance.

The rewriter emits, per answer, an spm-semiring provenance polynomial over the
statement tokens.  Given a probability for each token, the answer probability is
the weighted model count (WMC) of the polynomial's Boolean abstraction
(⊗→∧, ⊕→∨, ⊖(a,b)→a∧¬b).

This script contrasts three evaluations, to make the point behind the
Triple-Level paper:
  * NTI  — naive tuple-independence: read the polynomial extensionally
           (⊗→×, ⊕→independent-or, token→p). WRONG when a token is SHARED
           across the polynomial, because it double-counts.
  * WMC  — exact: treat the polynomial as a Boolean function over the tokens
           (a shared token is ONE event) and sum world probabilities.
  * PWE  — ground truth: plain SPARQL over every token subset.
WMC == PWE always; NTI diverges exactly when tokens are shared.
"""
import subprocess, itertools, re, os

ROOT = os.environ.get("NPCS_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JAR = ROOT + "/target/npcs-rewrite.jar"

EXAMPLES = [
  dict(name="share_union  (t1 shared across two UNION disjuncts)",
       data=ROOT+"/examples/data/share.standard.ttl",
       qfile=ROOT+"/examples/queries/prob/share_union.sparql",
       tokre=r't\d+',
       base={'t1':('A','p','B'),'t2':('B','q','D'),'t3':('B','r','D')},
       sel=['a','d'],
       query=('union',('bgp',[('?a','p','?b'),('?b','q','?d')]),
                      ('bgp',[('?a','p','?b'),('?b','r','?d')]))),
  dict(name="selfjoin     (t appears on the join diagonal ⊗(s1,s1))",
       data=ROOT+"/examples/data/selfjoin.standard.ttl",
       qfile=ROOT+"/examples/queries/prob/selfjoin.sparql",
       tokre=r's\d+',
       base={'s1':('A','p','B'),'s2':('C','p','B')},
       sel=['d'],
       query=('bgp',[('?x','p','?d'),('?y','p','?d')])),
]

# ---- plain SPARQL eval (for PWE ground truth) ----
def eval_bgp(pats,T):
    sols=[{}]
    for (s,p,o) in pats:
        nxt=[]
        for sol in sols:
            for (ts,tp,to) in T:
                b=dict(sol); ok=True
                for pv,tv in ((s,ts),(p,tp),(o,to)):
                    if pv.startswith('?'):
                        if pv in b and b[pv]!=tv: ok=False;break
                        b[pv]=tv
                    elif pv!=tv: ok=False;break
                if ok: nxt.append(b)
        sols=nxt
    return sols
def eval_q(q,T):
    if q[0]=='bgp':   return eval_bgp(q[1],T)
    if q[0]=='union': return eval_q(q[1],T)+eval_q(q[2],T)
    raise ValueError(q[0])
def answers(q,sel,T):
    return {frozenset((v,b['?'+v]) for v in sel if '?'+v in b) for b in eval_q(q,T)}
def pwe(ex,P):
    base,toks=ex['base'],list(ex['base'])
    prob={}
    for r in range(len(toks)+1):
        for S in itertools.combinations(toks,r):
            S=set(S); T={base[t] for t in S}
            w=1.0
            for t in toks: w*=P[t] if t in S else 1-P[t]
            for a in answers(ex['query'],ex['sel'],T): prob[a]=prob.get(a,0.0)+w
    return prob

# ---- parse provenance -> AST ----
def parse(s,tokre):
    s=s.replace('⊕(','OR(').replace('(⊗','AND(').replace('(⊕','OR(').replace('(⊖','MONUS(')
    s=s.replace('STR(','').replace(')',' ) ')
    toks=re.findall(r'OR\(|AND\(|MONUS\(|\)|'+tokre+r'|,',s); pos=[0]
    def p():
        t=toks[pos[0]]; pos[0]+=1
        if t in ('OR(','AND(','MONUS('):
            op=t[:-1]; args=[]
            while toks[pos[0]]!=')':
                if toks[pos[0]]==',': pos[0]+=1; continue
                args.append(p())
            pos[0]+=1
            if op=='OR': return ('or',args)
            if op=='AND':return ('and',args)
            return ('and',[args[0],('not',args[1])]) if len(args)>=2 else args[0]
        return ('tok',t)
    return p()

# ---- three evaluators ----
def nti(a,P):   # naive tuple-independence (extensional)
    k=a[0]
    if k=='tok': return P[a[1]]
    if k=='not': return 1-nti(a[1],P)
    if k=='and':
        r=1.0
        for c in a[1]: r*=nti(c,P)
        return r
    if k=='or':
        r=1.0
        for c in a[1]: r*=(1-nti(c,P))
        return 1-r
def truth(a,asn):
    k=a[0]
    if k=='tok': return asn[a[1]]
    if k=='not': return not truth(a[1],asn)
    if k=='and': return all(truth(c,asn) for c in a[1]) if a[1] else True
    if k=='or':  return any(truth(c,asn) for c in a[1]) if a[1] else False
def wmc(a,toks,P):
    tot=0.0
    for bits in itertools.product((0,1),repeat=len(toks)):
        asn=dict(zip(toks,bits))
        if truth(a,asn):
            w=1.0
            for t in toks: w*=P[t] if asn[t] else 1-P[t]
            tot+=w
    return tot

def run(ex):
    out=subprocess.run(["java","-cp",JAR,"npcs.RunExample","Standard",ex['data'],ex['qfile']],
                       capture_output=True,text=True).stdout
    res={}
    for line in out.splitlines():
        if '|' not in line or line.lstrip().startswith('#'): continue
        left,prov=line.split('|',1)
        key=frozenset((kv.split('=')[0].strip(),kv.split('=')[1]) for kv in left.split() if '=' in kv)
        res[key]=prov.strip()
    return res

for ex in EXAMPLES:
    toks=list(ex['base']); P={t:0.5 for t in toks}
    got=run(ex); truthP=pwe(ex,P)
    print("="*78); print(ex['name']); print("  token probabilities: all 0.5")
    for k,prov in got.items():
        ast=parse(prov,ex['tokre'])
        a_nti=nti(ast,P); a_wmc=wmc(ast,toks,P); a_pwe=truthP.get(k,0.0)
        err=(a_nti-a_wmc)/a_wmc*100 if a_wmc else 0
        print(f"  answer {dict(k)}")
        print(f"    provenance : {prov}")
        print(f"    NTI  (naive independence) = {a_nti:.4f}   <-- {'OVER' if err>0 else 'UNDER'}-estimates by {abs(err):.1f}%")
        print(f"    WMC  (exact, Boolean)     = {a_wmc:.4f}")
        print(f"    PWE  (ground truth)       = {a_pwe:.4f}   {'✓ WMC==PWE' if abs(a_wmc-a_pwe)<1e-9 else '✗'}")
