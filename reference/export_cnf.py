"""Export a provenance circuit to a WEIGHTED CNF (Tseitin), the input format for
d4 / c2d / gpmc. On a Linux/x86 box, d4 compiles this CNF to d-DNNF; we then read
the d-DNNF size (for the compiler-scaling figure) and its weighted model count
(which must equal our OBDD/SDD WMC and possible-world enumeration).

Encoding (Boolean abstraction of the circuit):
  leaf token  -> a primary CNF variable, weight (p, 1-p)
  const 1/0   -> a variable forced true / false
  plus  g=OR(C)   -> (¬g ∨ ⋁C) and (g ∨ ¬c) for each c            [g ↔ OR]
  times g=AND(C)  -> (g ∨ ⋁¬C) and (¬g ∨ c) for each c            [g ↔ AND]
  minus g=a∧¬b    -> (¬g ∨ a), (¬g ∨ ¬b), (g ∨ ¬a ∨ b)            [g ↔ a∧¬b]
  root            -> unit clause (root)
Gate/const variables get weight (1,1); the biconditionals functionally determine
them from the tokens, so the weighted model count over ALL variables equals the
answer probability.
"""
import json, itertools


def export(circ, root, P):
    """Return dict with dimacs text, weights {var:[wpos,wneg]}, and var_of map."""
    # collect gates reachable from root (DFS)
    seen, order = set(), []
    def dfs(n):
        if n in seen: return
        seen.add(n); op, pl = circ[n]
        if op in ("times", "plus"):
            for c in pl: dfs(c)
        elif op == "minus":
            dfs(pl[0]); dfs(pl[1])
        order.append(n)
    dfs(root)

    var_of, weights, clauses = {}, {}, []
    def v(n):
        if n not in var_of:
            var_of[n] = len(var_of) + 1
            op = circ[n][0]
            tok = circ[n][1] if op == "leaf" else None
            weights[var_of[n]] = [P[tok], 1 - P[tok]] if op == "leaf" else [1.0, 1.0]
        return var_of[n]

    for n in order:
        op, pl = circ[n]
        g = v(n)
        if op == "leaf":
            continue
        if op == "const":
            clauses.append([g] if pl else [-g])
        elif op == "plus":
            ch = [v(c) for c in pl]
            if not ch:
                clauses.append([-g])                       # empty OR = false
            else:
                clauses.append([-g] + ch)                  # ¬g ∨ ⋁C
                for c in ch: clauses.append([g, -c])       # g ∨ ¬c
        elif op == "times":
            ch = [v(c) for c in pl]
            if not ch:
                clauses.append([g])                        # empty AND = true
            else:
                clauses.append([g] + [-c for c in ch])     # g ∨ ⋁¬C
                for c in ch: clauses.append([-g, c])       # ¬g ∨ c
        elif op == "minus":
            a, b = v(pl[0]), v(pl[1])
            clauses += [[-g, a], [-g, -b], [g, -a, b]]
    clauses.append([var_of[root]])                         # assert root

    nv = len(var_of)
    lines = [f"p cnf {nv} {len(clauses)}"]
    for var, (wp, wn) in sorted(weights.items()):          # MC-competition weight lines
        lines.append(f"c p weight {var} {wp} 0")
        lines.append(f"c p weight {-var} {wn} 0")
    for cl in clauses:
        lines.append(" ".join(map(str, cl)) + " 0")
    return {"dimacs": "\n".join(lines) + "\n", "nvars": nv, "nclauses": len(clauses),
            "weights": weights, "clauses": clauses, "var_of": var_of}


def cnf_wmc_bruteforce(nvars, clauses, weights):
    """Independent WMC over the CNF (enumerate all vars). Small instances only —
    this validates the ENCODING without reusing the circuit evaluator."""
    total = 0.0
    for bits in itertools.product((0, 1), repeat=nvars):
        if all(any((lit > 0) == bool(bits[abs(lit) - 1]) for lit in cl) for cl in clauses):
            w = 1.0
            for var in range(1, nvars + 1):
                w *= weights[var][0] if bits[var - 1] else weights[var][1]
            total += w
    return total


if __name__ == "__main__":
    import rdflib, verify_all, compile_bdd, wmc
    C = rdflib.Namespace("urn:circuit:"); D = "urn:d:"
    def to_circ(nt):
        typ, feeds, tin, minus, ans = verify_all.load(nt)
        c = {}
        for t, ls in tin.items():
            for l in ls: c[l] = ("leaf", str(l).replace(D, ""))
        for n, tp in typ.items():
            if tp == C.Times: c[n] = ("times", tuple(sorted(tin.get(n, ()))))
            elif tp == C.Plus: c[n] = ("plus", tuple(sorted(feeds.get(n, ()))))
            elif tp == C.Minus: m = minus[n]; c[n] = ("minus", (m["m"], m["s"]))
        ref = set()
        for op, pl in c.values():
            if op in ("times", "plus"): ref |= set(pl)
            elif op == "minus": ref |= {pl[0], pl[1]}
        for r in ref: c.setdefault(r, ("plus", ()))
        return c, ans

    print("verify CNF encoding: brute CNF-WMC == OBDD-WMC == PWE")
    import os
    os.makedirs("cnf", exist_ok=True)
    manifest = []
    for name in ["drug", "selfjoin", "minus", "optional"]:
        s = verify_all.REG[name]; circ, ans = to_circ(s["nt"]); P = s["P"]
        truth = {frozenset((v.lstrip("?"), vv) for v, vv in k): p
                 for k, p in wmc.pwe(s["q"], s["sel"], s["base"], P).items()}
        for root, key in ans.items():
            e = export(circ, root, P)
            cnf_wmc = cnf_wmc_bruteforce(e["nvars"], e["clauses"], e["weights"])
            obdd = compile_bdd.probability(circ, root, P)[0]
            obdd_size = compile_bdd.probability(circ, root, P)[1]
            tp = truth.get(verify_all.parse_key(key), 0.0)
            tag = "OK" if abs(cnf_wmc - obdd) < 1e-9 and abs(cnf_wmc - tp) < 1e-9 else "FAIL"
            lbl = f"{name}_{sorted(dict(verify_all.parse_key(key)).items())}".replace(" ", "").replace("'", "")[:60]
            fn = "cnf/" + lbl + ".cnf"
            open(fn, "w").write(e["dimacs"])
            manifest.append({"instance": lbl, "cnf": os.path.basename(fn), "nvars": e["nvars"],
                             "nclauses": e["nclauses"], "expected_wmc": round(cnf_wmc, 9), "obdd_size": obdd_size})
            print(f"  [{name:8}] vars={e['nvars']:2} clauses={e['nclauses']:3}  "
                  f"CNF-WMC={cnf_wmc:.6f} OBDD={obdd:.6f} PWE={tp:.6f}  {tag}")
    json.dump(manifest, open("cnf/manifest.json", "w"), indent=2)
    print(f"\nWrote {len(manifest)} CNFs + cnf/manifest.json (expected_wmc + obdd_size per instance).")
    print("On Linux: python3 d4_pipeline.py   (compiles each with d4 -> d-DNNF size + WMC, checks vs manifest).")
