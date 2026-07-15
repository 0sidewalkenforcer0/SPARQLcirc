import sys
RS="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
OCC="http://example.org/occurrenceOf"        # must match engine SPARQL_STAR OCCURRENCE_OF
# Usage: reify.py <in.nt> <out> [--star | --namedgraph]
#   default      -> STANDARD reification (3 triples/fact: <t> rdf:subject/predicate/object ...)
#   --star       -> RDF-star             (1 quoted triple/fact: << s p o >> occ:occurrenceOf <t>)
#   --namedgraph -> named-graph          (1 quad/fact, N-Quads: <s> <p> <o> <t> . ; graph <t> = token)
args=[a for a in sys.argv[1:] if not a.startswith("--")]
star="--star" in sys.argv[1:]
namedgraph="--namedgraph" in sys.argv[1:]
inp, out = args[0], args[1]
n=0
with open(inp) as f, open(out,"w") as g:
    for line in f:
        line=line.strip()
        if not line.endswith("."): continue
        body=line[:-1].strip()
        parts=body.split(None,2)         # split on ANY whitespace: WatDiv .nt is TAB-separated (o may be a literal with spaces)
        if len(parts)<3: continue
        s,p,o=parts
        t=f"<urn:t:{n}>"
        if star:
            g.write(f"<< {s} {p} {o} >> <{OCC}> {t} .\n")
        elif namedgraph:
            g.write(f"{s} {p} {o} {t} .\n")          # N-Quads: 4th term = graph = provenance token
        else:
            g.write(f"{t} <{RS}subject> {s} .\n{t} <{RS}predicate> {p} .\n{t} <{RS}object> {o} .\n")
        n+=1
mode = "RDF-star" if star else "named-graph quad" if namedgraph else "reified"
mult = 1 if (star or namedgraph) else 3
print(f"reified {n} triples -> {out} ({mult*n} {mode} triples)")
