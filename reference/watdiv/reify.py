import sys
RS="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
inp, out = sys.argv[1], sys.argv[2]
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
        g.write(f"{t} <{RS}subject> {s} .\n{t} <{RS}predicate> {p} .\n{t} <{RS}object> {o} .\n")
        n+=1
print(f"reified {n} triples -> {out} ({3*n} reified triples)")
