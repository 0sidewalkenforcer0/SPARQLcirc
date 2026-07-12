"""G7 — Standard vs SPARQL-star reification size (bytes + triples/fact), on real data.

Standard: 1 fact `s p o` -> 3 triples (t rdf:subject s ; rdf:predicate p ; rdf:object o).
SPARQL-star: 1 fact -> 1 quoted triple `<< s p o >> occ:occurrenceOf t`.
Circuit-equivalence (same ⊕/⊗/⊖ under both schemes) is shown separately by RunExample — see G7_RESULTS.md:
  java -cp engine/target/npcs-rewrite.jar npcs.RunExample SPARQL_Star engine/examples/data/example.star.ttls  <q>
  java -cp engine/target/npcs-rewrite.jar npcs.RunExample Standard    engine/examples/data/example.standard.ttl <q>

  python3 g7_reification.py [raw.nt] [N]     # default: WatDiv 100M sample, 100k facts
"""
import sys, csv
RS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
OCC = "http://example.org/occurrenceOf"

def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "/mnt/nfs/home/ac145595/workspace/watdiv-data/watdiv.100M.nt"
    N = int(sys.argv[2]) if len(sys.argv) > 2 else 100_000
    raw_b = std_b = star_b = n = 0
    with open(src) as f:
        for line in f:
            if n >= N: break
            line = line.strip()
            if not line.endswith("."): continue
            parts = line[:-1].strip().split(None, 2)
            if len(parts) < 3: continue
            s, p, o = parts; t = f"<urn:t:{n}>"
            raw_b += len(line) + 1
            std_b += (len(f"{t} <{RS}subject> {s} .") + 1 + len(f"{t} <{RS}predicate> {p} .") + 1
                      + len(f"{t} <{RS}object> {o} .") + 1)
            star_b += len(f"<< {s} {p} {o} >> <{OCC}> {t} .") + 1
            n += 1
    rows = [
        dict(encoding="raw_nt",       bytes=raw_b,  vs_raw=1.0,               b_per_fact=raw_b // n,  triples_per_fact=1),
        dict(encoding="standard",     bytes=std_b,  vs_raw=round(std_b/raw_b,3), b_per_fact=std_b // n, triples_per_fact=3),
        dict(encoding="sparql_star",  bytes=star_b, vs_raw=round(star_b/raw_b,3),b_per_fact=star_b // n,triples_per_fact=1),
    ]
    print(f"G7 reification size on {n} facts ({src})\n")
    for r in rows:
        print(f"  {r['encoding']:12} {r['bytes']:>12} B  {r['vs_raw']:>5}x raw  {r['b_per_fact']:>4} B/fact  {r['triples_per_fact']} triple(s)/fact")
    print(f"\n  Standard / SPARQL-star : {std_b/star_b:.2f}x bytes, 3x triples")
    with open("g7_reification.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    print("wrote g7_reification.csv")

if __name__ == "__main__":
    main()
