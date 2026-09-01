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
    import verify_all, compile_bdd, wmc, circuit_io
    def _truth(s):   # term-aware PWE keys (matching circuit_io.answer_key over c:binding)
        out = {}
        for k, p in wmc.pwe(s["q"], s["sel"], s["base"], s["P"]).items():
            d = dict(k)
            out[circuit_io.answer_key({sv.lstrip("?"): (circuit_io.canon_iri("urn:d:" + d[sv]) if sv in d else "u")
                                       for sv in s["sel"]})] = p
        return out

    print("verify CNF encoding: brute CNF-WMC == OBDD-WMC == PWE")
    import os
    os.makedirs("cnf", exist_ok=True)
    manifest = []
    for name in ["drug", "selfjoin", "minus", "optional"]:
        s = verify_all.REG[name]
        circ, answers, bindings = circuit_io.parse(open(s["nt"]).read())
        Pf = {"urn:d:" + k: v for k, v in s["P"].items()}             # circuit leaves are urn:d: token IRIs
        truth = _truth(s)
        for g in answers:
            key = circuit_io.answer_key(bindings[g])
            e = export(circ, g, Pf)
            cnf_wmc = cnf_wmc_bruteforce(e["nvars"], e["clauses"], e["weights"])
            obdd, obdd_size = compile_bdd.probability(circ, g, Pf)
            tp = truth.get(key, 0.0)
            tag = "OK" if abs(cnf_wmc - obdd) < 1e-9 and abs(cnf_wmc - tp) < 1e-9 else "FAIL"
            lbl = (name + "_" + key).replace("|", "_").replace("=", "").replace("\x1f", "").replace(":", "")[:60]
            fn = "cnf/" + lbl + ".cnf"
            open(fn, "w").write(e["dimacs"])
            manifest.append({"instance": lbl, "cnf": os.path.basename(fn), "nvars": e["nvars"],
                             "nclauses": e["nclauses"], "expected_wmc": round(cnf_wmc, 9), "obdd_size": obdd_size})
            print(f"  [{name:8}] vars={e['nvars']:2} clauses={e['nclauses']:3}  "
                  f"CNF-WMC={cnf_wmc:.6f} OBDD={obdd:.6f} PWE={tp:.6f}  {tag}")
    json.dump(manifest, open("cnf/manifest.json", "w"), indent=2)
    print(f"\nWrote {len(manifest)} CNFs + cnf/manifest.json (expected_wmc + obdd_size per instance).")
    print("On Linux: python3 d4_pipeline.py   (compiles each with d4 -> d-DNNF size + WMC, checks vs manifest).")
