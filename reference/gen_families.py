"""Treewidth-controlled query/data families for the compilation experiments
(E4 tractability, E5 factored-vs-flat). WatDiv cannot sweep treewidth; these can.

Each family() returns (turtle_data, select_query, meta). Data is reified (one
token per edge) so CircuitRun/CircuitRewriter can build a circuit directly:

    java -cp ../engine/target/npcs-rewrite.jar npcs.circuit.CircuitRun \\
         Standard families/layered.ttl families/layered.rq

`meta` records the THEORETICAL treewidth of the answer's provenance formula, which
governs compiled size: d-DNNF is O(n·2^{O(tw)}) (linear in n), OBDD is n^{O(tw)}.

Families
    chain(n)            path of n edges              tw=1     (read-once sanity)
    star(b, d)          hub, b predicates × d edges   tw=1     (existential; E5)
    layered(depth, w)   s-t DAG, w-wide, depth-deep   tw≈w     (MAIN knob: w=tw, depth=n)
    grid(k)             k×k monotone grid             tw≈k     (2D growing-tw)
"""
import os, sys

RS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
PRE = f"@prefix : <urn:g#> .\n@prefix rdf: <{RS}> .\n"
QPRE = "PREFIX : <urn:g#>\n"

def _edge(tid, s, o, pred="e"):
    return f":t{tid} rdf:subject :{s} ; rdf:predicate :{pred} ; rdf:object :{o} .\n"

def _path_query(start, hops, proj):
    """A `hops`-length path query from node `start`, projecting variable `proj`."""
    lines = [f":{start} :e ?v1 ."] + [f"?v{i} :e ?v{i+1} ." for i in range(1, hops)]
    return f"{QPRE}SELECT ?{proj} WHERE {{\n  " + "\n  ".join(lines) + "\n}\n"

def chain(n):
    ttl = [PRE] + [_edge(i, f"c{i}", f"c{i+1}") for i in range(n)]
    return ("".join(ttl), _path_query("c0", n, f"v{n}"),
            dict(name=f"chain-{n}", tokens=n, answers=1, tw=1, deriv=1,
                 note="read-once path; tw=1"))

def star(branches, deg):
    ttl = [PRE]; tid = 0
    for j in range(branches):
        for m in range(deg):
            ttl.append(_edge(tid, "h", f"h_{j}_{m}", pred=f"p{j}")); tid += 1
    pats = "\n  ".join(f"?h :p{j} ?x{j} ." for j in range(branches))
    q = f"{QPRE}SELECT ?h WHERE {{\n  {pats}\n}}\n"
    return ("".join(ttl), q,
            dict(name=f"star-b{branches}-d{deg}", tokens=branches * deg, answers=1, tw=1,
                 deriv=deg ** branches,
                 note=f"existential cross-product: flat=deg^b={deg**branches}, "
                      f"factored=b·deg={branches*deg}"))

def layered(depth, width):
    ttl = [PRE]; tid = 0
    for a in range(width):                                   # S -> layer 1
        ttl.append(_edge(tid, "S", f"n1_{a}")); tid += 1
    for i in range(1, depth):                                # full bipartite between layers
        for a in range(width):
            for b in range(width):
                ttl.append(_edge(tid, f"n{i}_{a}", f"n{i+1}_{b}")); tid += 1
    return ("".join(ttl), _path_query("S", depth, f"v{depth}"),
            dict(name=f"layered-d{depth}-w{width}",
                 tokens=width + (depth - 1) * width * width, answers=width, tw=width,
                 deriv=width ** (depth - 1),
                 note=f"s-t DAG; tw≈width={width}; #deriv/answer=width^(depth-1)"))

def grid(k):
    ttl = [PRE]; tid = 0
    nd = lambda i, j: f"g{i}_{j}"
    for i in range(k):
        for j in range(k):
            if j + 1 < k: ttl.append(_edge(tid, nd(i, j), nd(i, j + 1))); tid += 1   # right
            if i + 1 < k: ttl.append(_edge(tid, nd(i, j), nd(i + 1, j))); tid += 1   # down
    hops = 2 * (k - 1)
    return ("".join(ttl), _path_query("g0_0", hops, f"v{hops}"),
            dict(name=f"grid-{k}", tokens=tid, answers=1, tw=k, deriv="C(2(k-1),k-1)",
                 note=f"monotone {k}x{k} grid; tw≈{k}; single corner answer"))

# default representative instances (experiment scripts import the functions and sweep)
DEFAULTS = [chain(6), star(3, 4), layered(4, 3), grid(3)]

def write(pairs, outdir):
    os.makedirs(outdir, exist_ok=True)
    print(f"{'family':<16} {'tokens':>7} {'answers':>7} {'tw':>4} {'deriv/ans':>12}   note")
    for ttl, q, m in pairs:
        open(os.path.join(outdir, m["name"] + ".ttl"), "w").write(ttl)
        open(os.path.join(outdir, m["name"] + ".rq"), "w").write(q)
        print(f"{m['name']:<16} {m['tokens']:>7} {str(m['answers']):>7} {str(m['tw']):>4} "
              f"{str(m['deriv']):>12}   {m['note']}")

if __name__ == "__main__":
    outdir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "families")
    write(DEFAULTS, outdir)
    print(f"\nwrote {len(DEFAULTS)} families to {outdir}/  (*.ttl data, *.rq query)")
    print("E4 sweep e.g.: [layered(d,2) for d in 2..8] (bounded tw), [layered(3,w) for w in 2..6] (growing tw)")
