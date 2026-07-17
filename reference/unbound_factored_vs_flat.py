"""factored's DESIGN win: UNBOUND reconvergent conjunctive queries, where flat's sum-of-products explodes
but factored variable-elimination stays polynomial.

The deployed rdfstar/wikidata experiments are all SOURCE-BOUND (selective) — a regime where flat is already
tiny, so factored can't show its advantage (and its current, non-source-restricted messages even hurt on a
selective chain; see rdfstar_factored). This experiment isolates the regime factored is FOR: an unbound
k-hop chain over a fully-connected LAYERED graph (maximal reconvergence). With W nodes per layer:
  * #derivations per (v0,vk) answer = W^(k-1)  (every choice of the k-1 interior nodes)  -> flat = SUM-OF-
    PRODUCTS enumerates all of them: ~W^(k+1) product gates (EXPONENTIAL in the path length k);
  * factored eliminates the interior variables into per-boundary marginals: ~k*W^2 gates (POLYNOMIAL).
Both compute the same answers; only the circuit SIZE (and build time) differ. This is the flat-vs-factored
crossover the paper needs to justify factored.

Runs CircuitRun in-memory (RDF4J MemoryStore is writable, so factored's feedback works; the circuit is
engine-agnostic — proven byte-identical across engines). Env: UFF_W (default 4), UFF_KS (default "2,3,4,5"),
UFF_OUT (default watdiv/unbound_factored_vs_flat.csv).
"""
import os, csv, tempfile, subprocess
import circuit_io

HERE = os.path.dirname(os.path.abspath(__file__))
JAR = os.path.join(HERE, "..", "engine", "target", "npcs-rewrite.jar")
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
W = int(os.environ.get("UFF_W", "4"))
KS = [int(k) for k in os.environ.get("UFF_KS", "2,3,4,5").split(",")]
OUT = os.environ.get("UFF_OUT", "watdiv/unbound_factored_vs_flat.csv")
COLS = ["k", "W", "answers", "flat_gates", "flat_ms", "factored_gates", "factored_ms",
        "flat_over_factored", "flat_answers_ok", "factored_answers_ok"]


def layered_data(k, w):
    """k+1 layers of w nodes; every node in layer i links (predicate <urn:p>) to every node in layer i+1.
    Standard-reified, one token <urn:t:N> per edge."""
    lines = ["@prefix rdf: <%s> ." % RDF]
    tok = 0
    for i in range(k):
        for a in range(w):
            for b in range(w):
                tok += 1
                lines.append("<urn:t:%d> rdf:subject <urn:n%d_%d> ; rdf:predicate <urn:p> ; rdf:object <urn:n%d_%d> ."
                             % (tok, i, a, i + 1, b))
    return "\n".join(lines) + "\n"


def khop_query(k):
    """Unbound k-hop chain, projecting only the endpoints v0, vk (interior v1..v(k-1) are join variables)."""
    pats = " ".join("?v%d <urn:p> ?v%d ." % (i, i + 1) for i in range(k))
    return "SELECT ?v0 ?v%d WHERE { %s }\n" % (k, pats)


def run(mode, data_path, query_path):
    r = subprocess.run(["java", "-cp", JAR, "npcs.circuit.CircuitRun", "--construction=" + mode,
                        "Standard", data_path, query_path], capture_output=True, text=True, timeout=600)
    ms = next((int(l.split(":", 1)[1]) for l in r.stderr.splitlines() if l.startswith("# construction_ms:")), None)
    if ms is None:
        raise RuntimeError(f"{mode} failed: {r.stderr[-800:]}")
    circ, answers, _ = circuit_io.parse(r.stdout.splitlines())
    gates = sum(1 for op, _ in circ.values() if op in ("times", "plus", "minus"))
    return ms, gates, len(answers)


def main():
    print(f"factored's design win — UNBOUND reconvergent k-hop over a fully-connected layered graph (W={W})\n", flush=True)
    rows = []
    fh = open(OUT, "w", newline=""); w = csv.DictWriter(fh, fieldnames=COLS, extrasaction="ignore", restval="")
    w.writeheader(); fh.flush()
    for k in KS:
        data = tempfile.NamedTemporaryFile("w", suffix=".ttl", delete=False); data.write(layered_data(k, W)); data.close()
        query = tempfile.NamedTemporaryFile("w", suffix=".rq", delete=False); query.write(khop_query(k)); query.close()
        f_ms, f_gates, f_ans = run("flat", data.name, query.name)
        x_ms, x_gates, x_ans = run("factored", data.name, query.name)
        expect = W * W                                  # (v0, vk) pairs, all reachable in a full layered graph
        row = dict(k=k, W=W, answers=expect, flat_gates=f_gates, flat_ms=f_ms,
                   factored_gates=x_gates, factored_ms=x_ms,
                   flat_over_factored=round(f_gates / x_gates, 1) if x_gates else 0,
                   flat_answers_ok=(f_ans == expect), factored_answers_ok=(x_ans == expect))
        rows.append(row); w.writerow(row); fh.flush()
        print(f"  k={k} W={W}: flat={f_gates:>6} gates/{f_ms:>5}ms   factored={x_gates:>4} gates/{x_ms:>5}ms   "
              f"flat/factored={row['flat_over_factored']:>6}x   answers={x_ans}/{expect} "
              f"{'OK' if row['flat_answers_ok'] and row['factored_answers_ok'] else 'ANSWER-MISMATCH'}", flush=True)
    fh.close()
    print(f"\nwrote {OUT} ({len(rows)} rows)", flush=True)
    print("flat = sum-of-products (exponential in path length); factored = variable elimination (polynomial).")


if __name__ == "__main__":
    main()
