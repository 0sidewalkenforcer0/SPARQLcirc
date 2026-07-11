"""Reify a Wikidata TRUTHY graph (wdt: direct properties, e.g. the WDBench graph) into the native
statement form the engine's `Wikidata` reification scheme queries — matching NPCS's Wikidatareal:

    <s> <wdt:P> <o> .   ->   <s> <p:P> <urn:wds:N> .   <urn:wds:N> <ps:P> <o> .

so the fresh statement node <urn:wds:N> is the provenance token. wdt: (prop/direct) maps to
p: (prop/) for the claim and ps: (prop/statement/) for the value. Streaming, O(1) memory.

Usage:  python3 reify_wikidata.py <truthy.nt> <out.statements.nt>
Only wdt: triples are reified; any non-direct triple is passed through unchanged (WDBench is wdt:-only).
"""
import sys

WDT = "http://www.wikidata.org/prop/direct/"
P   = "http://www.wikidata.org/prop/"
PS  = "http://www.wikidata.org/prop/statement/"

def main(inp, out):
    n = 0; passed = 0
    with open(inp) as f, open(out, "w") as g:
        for line in f:
            line = line.rstrip("\n")
            if not line.endswith("."):
                continue
            parts = line[:-1].split(None, 2)                 # s, p, o(rest)
            if len(parts) < 3:
                continue
            s, p, o = parts[0], parts[1], parts[2].strip()
            pi = p.strip("<>")
            if pi.startswith(WDT):
                local = pi[len(WDT):]
                st = f"<urn:wds:{n}>"
                g.write(f"{s} <{P}{local}> {st} .\n{st} <{PS}{local}> {o} .\n")
                n += 1
            else:
                g.write(line + "\n"); passed += 1
    print(f"reified {n} truthy triples -> {2*n} statement triples ({passed} non-wdt passed through) -> {out}")

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: reify_wikidata.py <truthy.nt> <out.statements.nt>"); sys.exit(2)
    main(sys.argv[1], sys.argv[2])
