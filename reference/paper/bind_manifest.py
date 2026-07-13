"""R9.0 — freeze the performance workload manifest by re-binding the baseline templates to OUR WatDiv.

The SPARQLprov artifact ships the canonical WatDiv performance queries (L1-L5, S1-S7, F1-F5, C1-C3) plus
its five OPTIONAL templates (O1-O5). Every template's *placeholder* constant is bound to SPARQLprov's own
WatDiv generation (e.g. wsdbm:Retailer8811, wsdbm:Website10096) — constants that DO NOT EXIST in our
independently-generated WatDiv. This harness re-binds each placeholder against our data with a single
deterministic policy, so all four engines execute the identical query (never each engine's own LIMIT 1).

Placeholder vs structural constant (the rule, verified against the WatDiv testsuite):
  A wsdbm-entity constant in a SPARQLprov query is the *instance placeholder* iff it does NOT appear
  literally in the WatDiv testsuite template (where it would be a `%vN%` slot). Constants that appear
  literally in the template (wsdbm:Product0, wsdbm:Role2, wsdbm:Country1, wsdbm:Language0,
  wsdbm:ProductCategory2, wsdbm:Country5 in C2) are STRUCTURAL and kept verbatim. C1/C3 are fully unbound.
  O1-O5 have no testsuite template; their single wsdbm-entity constant is the placeholder.

Binding policy (deterministic, engine-agnostic, stored in the manifest):
  Replace the placeholder by a variable ?__b, translate the pattern to the Standard-reification R-form
  (reify_query, the same rdf:subject/predicate/object scheme our repos use), and take the MIN-IRI value of
  ?__b that yields a full match:  SELECT ?__b {<R-form>} ORDER BY ?__b LIMIT 1.  Guaranteed non-empty and
  reproducible.  Bound PER SCALE (10M and 100M are independent generations -> different entity sets).

Output:
  reference/paper/queries/watdiv/{10M,100M}/{class}/{template}.rq   -- bound BASE SELECT (full IRIs)
  reference/paper/workload_manifest.csv                             -- one row per (template, scale)

  python3 bind_manifest.py --scales 10M           # validate fast on 10M
  python3 bind_manifest.py --scales 10M,100M      # full freeze
"""
import os, sys, csv, hashlib, argparse, subprocess, urllib.parse, re

HERE = os.path.dirname(os.path.abspath(__file__))
REF = os.path.dirname(HERE)                                   # reference/
sys.path.insert(0, REF)
import reify_query                                            # the R-form rewriter (R9.2 foundation)

SP = "/mnt/nfs/home/ac145595/workspace/sparqlprov/SPARQLprov-experiments/SPARQLprov/queries/watdiv"
TS = "/mnt/nfs/home/ac145595/workspace/watdiv-data/watdiv/testsuite"
GDB = "http://localhost:7200/repositories"
WSDBM = "http://db.uwaterloo.ca/~galuc/wsdbm/"
RS = reify_query.RS                                           # rdf: reification namespace (s/p/o)
SCALE_REPO = {"10M": "watdiv", "100M": "watdiv100m"}          # our reified repos
PROBE_TIMEOUT = 240
CAND_K = 40                                                   # min-IRI candidates to try before full-pattern fallback

LSFC = {"L": [f"L{i}" for i in range(1, 6)], "S": [f"S{i}" for i in range(1, 8)],
        "F": [f"F{i}" for i in range(1, 6)], "C": [f"C{i}" for i in range(1, 4)],
        "O": [f"O{i}" for i in range(1, 6)]}

# --- our MINUS class M (SPARQLprov has none). Guarded, non-monotone; unbound (no %placeholder%). ---
M_QUERIES = {
    # M1 = the E6 M-minus shape: users who LIKE a product but did NOT purchase it (compound subtrahend).
    "M1": """SELECT ?v0 ?v1 WHERE {
  ?v0 <http://db.uwaterloo.ca/~galuc/wsdbm/likes> ?v1 .
  MINUS { ?v0 <http://db.uwaterloo.ca/~galuc/wsdbm/makesPurchase> ?p . ?p <http://db.uwaterloo.ca/~galuc/wsdbm/purchaseFor> ?v1 }
}""",
    # M2 = users who subscribe to a website but do NOT like anything.
    "M2": """SELECT ?v0 ?v1 WHERE {
  ?v0 <http://db.uwaterloo.ca/~galuc/wsdbm/subscribes> ?v1 .
  MINUS { ?v0 <http://db.uwaterloo.ca/~galuc/wsdbm/likes> ?x }
}""",
    # M3 = reviewed products that were NOT purchased (product has a review, minus purchased ones).
    "M3": """SELECT ?v0 ?v1 WHERE {
  ?v0 <http://purl.org/stuff/rev#hasReview> ?v1 .
  MINUS { ?p <http://db.uwaterloo.ca/~galuc/wsdbm/purchaseFor> ?v0 }
}""",
    # M4 = purchasing users who have NO friend (guarded on ?v0).
    "M4": """SELECT ?v0 ?v1 WHERE {
  ?v0 <http://db.uwaterloo.ca/~galuc/wsdbm/makesPurchase> ?v1 .
  MINUS { ?v0 <http://db.uwaterloo.ca/~galuc/wsdbm/friendOf> ?f }
}""",
    # M5 = users who like a product but do NOT subscribe to any website.
    "M5": """SELECT ?v0 ?v1 WHERE {
  ?v0 <http://db.uwaterloo.ca/~galuc/wsdbm/likes> ?v1 .
  MINUS { ?v0 <http://db.uwaterloo.ca/~galuc/wsdbm/subscribes> ?w }
}""",
}

class ProbeTimeout(Exception):
    pass

def sparql(repo, query, timeout=PROBE_TIMEOUT):
    """Run a SELECT over a GraphDB repo; return list of first-column values (CSV, header dropped).
    curl --max-time exhaustion (rc 28) is surfaced as ProbeTimeout so callers can fall back, not crash."""
    r = subprocess.run(
        ["curl", "-s", "--max-time", str(timeout), f"{GDB}/{repo}",
         "--data-urlencode", "query=" + query, "-H", "Accept: text/csv"],
        capture_output=True, text=True, timeout=timeout + 20)
    if r.returncode == 28:
        raise ProbeTimeout(f"{repo}: query exceeded {timeout}s")
    if r.returncode != 0:
        raise RuntimeError(f"curl rc={r.returncode} on {repo}: {r.stderr[-300:]}")
    lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
    if lines and lines[0].strip() in ("__b", "b", "n"):
        lines = lines[1:]
    return [ln.strip().strip('"') for ln in lines]

def ask(repo, ask_query, timeout=120):
    """Run an ASK; return bool. Uses text/boolean so emptiness is UNAMBIGUOUS (a SELECT's CSV header line
    would otherwise be miscounted as a result row)."""
    r = subprocess.run(
        ["curl", "-s", "--max-time", str(timeout), f"{GDB}/{repo}",
         "--data-urlencode", "query=" + ask_query, "-H", "Accept: text/boolean"],
        capture_output=True, text=True, timeout=timeout + 20)
    if r.returncode == 28:
        raise ProbeTimeout(f"{repo}: ASK exceeded {timeout}s")
    if r.returncode != 0:
        raise RuntimeError(f"curl rc={r.returncode} on {repo}: {r.stderr[-300:]}")
    return "true" in r.stdout.lower()

def reified_ask(query_text):
    """Reify a SELECT (Standard s/p/o) and turn it into an ASK over the same body."""
    rform = reify_query.reify(query_text)
    return re.sub(r"SELECT\b.*?\bWHERE", "ASK WHERE", rform, count=1, flags=re.S)

def type_prefix(ph_iri):
    """IRI prefix for the placeholder's entity CLASS, e.g. wsdbm:ProductCategory4 -> wsdbm:ProductCategory.
    Essential for rdf:type placeholders (S3/S5): the rdf:type objects include every class, so candidates
    must be constrained to the mapped type, not just the min-IRI rdf:type object."""
    loc = ph_iri[len(WSDBM):]
    m = re.match(r"([A-Za-z]+)", loc)
    return WSDBM + m.group(1) if m else WSDBM

def single_triple_candidates(sparql_text, ph_iri, repo, k=CAND_K):
    """The k min-IRI DISTINCT placeholder values (of the RIGHT TYPE) from the SINGLE reified triple that
    contains it (predicate-indexed + type-prefix filter -> fast). The true min-IRI full-match is among
    these unless the k smallest all lack a full match (then probe_binding falls back to the full pattern)."""
    line = next((ln for ln in sparql_text.splitlines() if f"<{ph_iri}>" in ln), None)
    if line is None:
        return []
    toks = line.strip().rstrip(" .").split(None, 2)                # "S P O"
    if len(toks) != 3:
        return []
    s, p, o = toks
    pred = p.strip("<>")
    if o.strip() == f"<{ph_iri}>":                                 # placeholder in OBJECT position
        pat = f"?__x <{RS}predicate> <{pred}> ; <{RS}object> ?__b ."
    elif s.strip() == f"<{ph_iri}>":                               # placeholder in SUBJECT position
        pat = f"?__x <{RS}predicate> <{pred}> ; <{RS}subject> ?__b ."
    else:
        return []
    flt = f'FILTER(STRSTARTS(STR(?__b), "{type_prefix(ph_iri)}"))'  # constrain to the mapped entity class
    probe = f"SELECT DISTINCT ?__b WHERE {{ {pat} {flt} }} ORDER BY ?__b LIMIT {k}\n"
    return sparql(repo, probe, timeout=180)

def full_match(sparql_text, ph_iri, cand, repo, timeout=120):
    """Does the FULL query have a solution when ph_iri := cand? (ASK; constant is selective -> fast)."""
    bound = sparql_text.replace(f"<{ph_iri}>", f"<{cand}>")
    return ask(repo, reified_ask(bound), timeout=timeout)

def testsuite_literals(template):
    """wsdbm-entity constants appearing LITERALLY in the WatDiv testsuite template (structural, keep)."""
    p = os.path.join(TS, template + ".txt")
    if not os.path.exists(p):
        return set()                                          # O templates have no testsuite entry
    txt = open(p).read()
    # testsuite uses prefixed names: wsdbm:Product0 etc.
    return set(re.findall(r"wsdbm:([A-Za-z]+[0-9]+)", txt))

def placeholder_iri(template, sparql_text):
    """The single placeholder IRI to re-bind, or None if the template is unbound/structural-only.

    Placeholder = a wsdbm:<Entity><N> full-IRI constant that is NOT a structural literal of the template.
    """
    structural = testsuite_literals(template)
    consts = re.findall(r"<" + re.escape(WSDBM) + r"([A-Za-z]+[0-9]+)>", sparql_text)
    cand = [c for c in dict.fromkeys(consts) if c not in structural]      # order-preserving, dedup
    if not cand:
        return None
    if len(cand) > 1:
        raise RuntimeError(f"{template}: ambiguous placeholder candidates {cand} (structural={structural})")
    return WSDBM + cand[0]

def full_pattern_probe(sparql_text, ph_iri, repo):
    """Exhaustive fallback: min-IRI ?__b over the FULL reified pattern (correct but a big ORDER BY join)."""
    probe_base = sparql_text.replace(f"<{ph_iri}>", "?__b")
    probe_base = re.sub(r"SELECT\b.*?\bWHERE", "SELECT ?__b WHERE", probe_base, count=1, flags=re.S)
    rform = reify_query.reify(probe_base).rstrip()
    vals = sparql(repo, rform + "\nORDER BY ?__b LIMIT 1\n")
    return vals[0] if vals else None

def probe_binding(template, sparql_text, ph_iri, repo):
    """Deterministic re-binding: the MIN-IRI value of the placeholder that yields a FULL match. Computed
    cheap-first (enumerate min-IRI candidates from the placeholder's own triple, take the first whose fixed
    substitution makes the full query non-empty) -- equivalent to the full-pattern min-match but fast at
    100M because a FIXED constant is selective. Falls back to the exhaustive full-pattern ORDER BY if the
    first CAND_K candidates all lack a full match. Returns (chosen_iri, note)."""
    cands = single_triple_candidates(sparql_text, ph_iri, repo)
    for c in cands:
        try:
            if full_match(sparql_text, ph_iri, c, repo):
                return c, ""
        except ProbeTimeout:
            continue                                           # this candidate's verify was slow; try next
    # none of the first CAND_K min-IRI candidates verified -> exhaustive fallback
    v = full_pattern_probe(sparql_text, ph_iri, repo)
    if v is None:
        raise RuntimeError(f"{template} @ {repo}: no binding of {ph_iri} yields a full match")
    return v, f"full-pattern fallback (first {len(cands)} min-IRI cands had no full match)"

def verify_nonempty(query_text, repo):
    """R-form of the (possibly-unbound / MINUS) base query has a solution? (ASK). Returns note ('' if
    non-empty, 'EMPTY on our data', or a timeout note — the query is kept either way)."""
    try:
        return "" if ask(repo, reified_ask(query_text), timeout=180) else "EMPTY on our data"
    except ProbeTimeout:
        return "non-emptiness unverified at scale (standard query kept)"

def sha256_file(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scales", default="10M,100M")
    args = ap.parse_args()
    scales = [s.strip() for s in args.scales.split(",") if s.strip()]

    rows = []
    qdir_root = os.path.join(HERE, "queries", "watdiv")
    for scale in scales:
        repo = SCALE_REPO[scale]
        # --- L/S/F/C/O from the SPARQLprov artifact ---
        for cls, templates in LSFC.items():
            for tmpl in templates:
                src = os.path.join(SP, tmpl + ".sparql")
                text = open(src).read().strip()
                ph = placeholder_iri(tmpl, text) if cls != "O" else \
                     (WSDBM + re.findall(r"<" + re.escape(WSDBM) + r"([A-Za-z]+[0-9]+)>", text)[0]
                      if re.findall(r"<" + re.escape(WSDBM) + r"([A-Za-z]+[0-9]+)>", text) else None)
                policy, note = "unbound (no placeholder)", ""
                bound = text
                if ph is not None:
                    chosen, note = probe_binding(tmpl, text, ph, repo)
                    bound = text.replace(f"<{ph}>", f"<{chosen}>")
                    policy = f"rebind {ph.split('/')[-1]} -> {chosen.split('/')[-1]} (min-IRI full-match)"
                else:
                    note = verify_nonempty(text, repo)
                outdir = os.path.join(qdir_root, scale, cls)
                os.makedirs(outdir, exist_ok=True)
                qf = os.path.join(outdir, tmpl + ".rq")
                open(qf, "w").write(bound + "\n")
                rows.append(dict(suite="watdiv-perf", **{"class": cls}, template=tmpl, instance="00",
                                 query_file=os.path.relpath(qf, REF), query_sha256=sha256_file(qf),
                                 scale=scale, bound_policy=policy, notes=note))
                print(f"  [{scale}] {tmpl:3} {policy}{'  !!'+note if note else ''}")
        # --- our M class ---
        for tmpl, text in M_QUERIES.items():
            text = text.strip()
            note = verify_nonempty(text, repo)
            outdir = os.path.join(qdir_root, scale, "M"); os.makedirs(outdir, exist_ok=True)
            qf = os.path.join(outdir, tmpl + ".rq"); open(qf, "w").write(text + "\n")
            rows.append(dict(suite="watdiv-perf", **{"class": "M"}, template=tmpl, instance="00",
                             query_file=os.path.relpath(qf, REF), query_sha256=sha256_file(qf),
                             scale=scale, bound_policy="unbound MINUS (ours)", notes=note))
            print(f"  [{scale}] {tmpl:3} unbound MINUS (ours){'  !!'+note if note else ''}")

    out = os.path.join(HERE, "workload_manifest.csv")
    cols = ["suite", "class", "template", "instance", "query_file", "query_sha256", "scale", "bound_policy", "notes"]
    with open(out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols); w.writeheader(); w.writerows(rows)
    # per-class instance counts (the "5+ per class or record exact count" requirement)
    from collections import Counter
    for scale in scales:
        c = Counter(r["class"] for r in rows if r["scale"] == scale)
        print(f"\n{scale} instances/class: " + "  ".join(f"{k}={c[k]}" for k in "LSFCOM"))
    print(f"wrote {out}  ({len(rows)} rows)")

if __name__ == "__main__":
    main()
