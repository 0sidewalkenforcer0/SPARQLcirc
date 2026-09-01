#!/usr/bin/env python3
"""Independent correctness check for npcs-rewrite-clean.

For each (query, reification scheme):
  * GROUND TRUTH: possible-world enumeration (PWE). For every subset S of the
    provenance tokens, evaluate the query with a small but general PLAIN SPARQL
    evaluator (BGP join / UNION / OPTIONAL / MINUS) over the base triples that S
    activates, and accumulate P(answer).
  * UNDER TEST: run the NPCS-rewritten query (npcs.RunExample), parse the emitted
    ⊕/⊗/⊖ provenance of each answer into a Boolean function
    (⊗→AND, ⊕→OR, ⊖(a,b)→a∧¬b — the Boolean abstraction of the spm-semiring),
    and weighted-model-count it.
Correct iff PWE(answer) == WMC(prov(answer)) for every answer, every assignment.
"""
import subprocess, itertools, re, sys, os

ROOT = os.environ.get("NPCS_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JAR = ROOT + "/target/npcs-rewrite.jar"

# ------------------------------- datasets -------------------------------------
DS1 = {  # the paper example + Bob/Carol
    'tokens': ['u1','u2','u3','u4','u5'],
    'base': {'u1':('Alice','likes','pasta'), 'u2':('Alice','likes','pasta'),
             'u3':('Alice','livesIn','Italy'),'u4':('Bob','likes','pasta'),
             'u5':('Carol','livesIn','Italy')},
    'std': ROOT+"/examples/data/example.standard.ttl",
    'star':ROOT+"/examples/data/example.star.ttls",
}
DS2 = {  # richer: chains, parallel edge, dangling node F
    'tokens': ['t1','t2','t3','t4','t5','t6','t7'],
    'base': {'t1':('A','p','B'),'t2':('A','p','B'),'t3':('B','q','C'),
             't4':('A','p','D'),'t5':('D','q','C'),'t6':('A','r','E'),
             't7':('A','p','F')},
    'std': ROOT+"/examples/data/example2.standard.ttl",
    'star':ROOT+"/examples/data/example2.star.ttls",
}

# query DSL:  ('bgp',[patterns]) | ('union',q,q) | ('optional',q,q) | ('minus',q,q)
# patterns are (s,p,o); variables start with '?'.
TESTS = [
  # dataset, name, qfile, select-vars, query
  (DS1,'monotonic/and',        'monotonic/and',        ['x'],
       ('bgp',[('?x','likes','pasta'),('?x','livesIn','Italy')])),
  (DS1,'monotonic/union',      'monotonic/union',      ['x'],
       ('union',('bgp',[('?x','likes','pasta')]),('bgp',[('?x','livesIn','Italy')]))),
  (DS1,'nonmonotonic/minus',   'nonmonotonic/minus',   ['x'],
       ('minus',('bgp',[('?x','likes','pasta')]),('bgp',[('?x','livesIn','Italy')]))),
  (DS1,'nonmonotonic/optional','nonmonotonic/optional',['x','country'],
       ('optional',('bgp',[('?x','likes','pasta')]),('bgp',[('?x','livesIn','?country')]))),
  (DS2,'multipattern/chain',    'multipattern/chain',    ['x','z'],
       ('bgp',[('?x','p','?y'),('?y','q','?z')])),
  (DS2,'multipattern/union2',   'multipattern/union2',   ['x'],
       ('union',('bgp',[('?x','p','?y'),('?y','q','?z')]),('bgp',[('?x','r','?e')]))),
  (DS2,'multipattern/minus2',   'multipattern/minus2',   ['x','y'],
       ('minus',('bgp',[('?x','p','?y')]),('bgp',[('?y','q','?z')]))),
  (DS2,'multipattern/optional2','multipattern/optional2',['x','y','z'],
       ('optional',('bgp',[('?x','p','?y')]),('bgp',[('?y','q','?z')]))),
  (DS2,'multipattern/minus_union','multipattern/minus_union',['x'],
       ('minus',('bgp',[('?x','p','?y')]),
                ('union',('bgp',[('?y','q','?z')]),('bgp',[('?y','r','?w')])))),
]

# ------------------------- general plain SPARQL eval --------------------------
def eval_bgp(patterns, T):
    sols = [dict()]
    for (s, p, o) in patterns:
        nxt = []
        for sol in sols:
            for (ts, tp, to) in T:
                b = dict(sol); ok = True
                for pv, tv in ((s, ts), (p, tp), (o, to)):
                    if pv.startswith('?'):
                        if pv in b and b[pv] != tv: ok = False; break
                        b[pv] = tv
                    elif pv != tv:
                        ok = False; break
                if ok: nxt.append(b)
        sols = nxt
    return sols

def compat(a, b): return all(a[k] == b[k] for k in a if k in b)

def eval_q(q, T):
    k = q[0]
    if k == 'bgp':      return eval_bgp(q[1], T)
    if k == 'union':    return eval_q(q[1], T) + eval_q(q[2], T)
    if k == 'optional':
        res = []
        for b in eval_q(q[1], T):
            ext = [m for m in eval_q(q[2], T) if compat(b, m)]
            res.extend([{**b, **m} for m in ext] if ext else [b])
        return res
    if k == 'minus':
        rhs = eval_q(q[2], T); res = []
        for b in eval_q(q[1], T):
            if not any((set(b) & set(m)) and compat(b, m) for m in rhs):
                res.append(b)
        return res
    raise ValueError(k)

def answers(q, sel, T):
    """set of frozenset((var,val)) projected on sel, dropping unbound vars,
       deduplicated (SPARQL set semantics on the provenance-annotated relation)."""
    out = set()
    for b in eval_q(q, T):
        out.add(frozenset((v, b['?' + v]) for v in sel if '?' + v in b))
    return out

# ------------------------------- PWE ------------------------------------------
def pwe(ds, q, sel, P):
    base, toks = ds['base'], ds['tokens']
    prob = {}
    for r in range(len(toks) + 1):
        for S in itertools.combinations(toks, r):
            S = set(S)
            T = {base[t] for t in S}
            w = 1.0
            for t in toks: w *= P[t] if t in S else (1 - P[t])
            for a in answers(q, sel, T):
                prob[a] = prob.get(a, 0.0) + w
    return prob

# ------------------- parse provenance polynomial -> Boolean AST ----------------
def parse_prov(s, tokre):
    s = s.replace('⊕(', 'OR(').replace('(⊗', 'AND(').replace('(⊕', 'OR(').replace('(⊖', 'MONUS(')
    s = s.replace('STR(', '').replace(')', ' ) ')
    toks = re.findall(r'OR\(|AND\(|MONUS\(|\)|' + tokre + r'|,', s)
    pos = [0]
    def parse():
        t = toks[pos[0]]; pos[0] += 1
        if t in ('OR(', 'AND(', 'MONUS('):
            op, args = t[:-1], []
            while toks[pos[0]] != ')':
                if toks[pos[0]] == ',': pos[0] += 1; continue
                args.append(parse())
            pos[0] += 1
            if op == 'OR':  return ('or', args)
            if op == 'AND': return ('and', args)
            return ('and', [args[0], ('not', args[1])]) if len(args) >= 2 else args[0]
        return ('tok', t)
    return parse()

def eval_ast(a, asn):
    k = a[0]
    if k == 'tok':  return asn[a[1]]
    if k == 'not':  return not eval_ast(a[1], asn)
    if k == 'or':   return any(eval_ast(x, asn) for x in a[1]) if a[1] else False
    if k == 'and':  return all(eval_ast(x, asn) for x in a[1]) if a[1] else True
    raise ValueError(k)

def wmc(ast, ds, P):
    toks = ds['tokens']; total = 0.0
    for bits in itertools.product((0, 1), repeat=len(toks)):
        asn = dict(zip(toks, bits))
        if eval_ast(ast, asn):
            w = 1.0
            for t in toks: w *= P[t] if asn[t] else (1 - P[t])
            total += w
    return total

# --------------------------- run rewritten query ------------------------------
def run_query(scheme, ds, qrel):
    data = ds['std'] if scheme == 'Standard' else ds['star']
    qfile = ROOT + "/examples/queries/" + qrel + ".sparql"
    out = subprocess.run(["java", "-cp", JAR, "npcs.RunExample", scheme, data, qfile],
                         capture_output=True, text=True, timeout=120).stdout
    res = {}
    for line in out.splitlines():
        if '|' not in line or line.lstrip().startswith('#'): continue
        left, prov = line.split('|', 1)
        binds = frozenset((kv.split('=')[0].strip(), kv.split('=')[1])
                          for kv in left.split() if '=' in kv)
        res[binds] = prov.strip()
    return res

def main():
    trials = [
        {'default': 0.5},
        {'default': 0.3},
        {'default': 0.8},
    ]
    total = ok = 0; fails = []
    for ds, name, qrel, sel, q in TESTS:
        tokre = r'u\d+' if ds is DS1 else r't\d+'
        for scheme in ('Standard', 'SPARQL_Star'):
            got = run_query(scheme, ds, qrel)
            for i, tpl in enumerate(trials):
                # per-token probabilities (vary by index so trials differ)
                P = {t: round(0.2 + 0.6 * (((j + i) % 5) / 4.0), 3)
                     for j, t in enumerate(ds['tokens'])}
                truth = pwe(ds, q, sel, P)
                keys = set(got) | {k for k, v in truth.items() if v > 1e-12}
                for k in keys:
                    total += 1
                    exp = truth.get(k, 0.0)
                    if k not in got:
                        fails.append(f"{scheme} {name} {dict(k)}: PWE={exp:.4f} NOT returned"); continue
                    val = wmc(parse_prov(got[k], tokre), ds, P)
                    if abs(val - exp) < 1e-9: ok += 1
                    else: fails.append(f"{scheme} {name} {dict(k)}: PWE={exp:.6f} WMC={val:.6f} prov={got[k]}")
    for f in fails[:20]: print("  ✗", f)
    print(f"\n{ok}/{total} answer-probability checks passed "
          f"({len(TESTS)} queries × 2 schemes × {len(trials)} assignments).")
    sys.exit(0 if ok == total else 1)

if __name__ == "__main__":
    main()
