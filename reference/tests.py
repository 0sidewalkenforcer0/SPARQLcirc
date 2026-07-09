"""Correctness battery: circuit-WMC vs possible-world enumeration across the
non-monotone fragment, several datasets, several probability assignments."""
import gates, gamma, wmc

DS_paper = {
    "u1": ("Alice", "likes", "pasta"), "u2": ("Alice", "likes", "pasta"),
    "u3": ("Alice", "livesIn", "Italy"), "u4": ("Bob", "likes", "pasta"),
    "u5": ("Carol", "livesIn", "Italy"),
}
DS_chain = {
    "t1": ("A", "p", "B"), "t2": ("A", "p", "B"), "t3": ("B", "q", "C"),
    "t4": ("A", "p", "D"), "t5": ("D", "q", "C"), "t6": ("A", "r", "E"),
    "t7": ("A", "p", "F"),
}
DS_share = {"t1": ("A", "p", "B"), "t2": ("B", "q", "D"), "t3": ("B", "r", "D")}
DS_self  = {"s1": ("A", "p", "B"), "s2": ("C", "p", "B")}
# Property-path datasets. DS_cyc has a CYCLE (A->C->A and A->B->C->A): naive walk
# enumeration is infinite, but the level-indexed circuit is finite/polynomial.
DS_cyc   = {"e1": ("A", "p", "B"), "e2": ("B", "p", "C"),
            "e3": ("A", "p", "C"), "e4": ("C", "p", "A")}
DS_pth   = {"a": ("X", "p", "Y"), "b": ("Y", "q", "Z"),
            "c": ("X", "r", "Z"), "d": ("Z", "q", "Y")}

TESTS = [
  ("and",       DS_paper, ["?x"],
     ("bgp", [("?x", "likes", "pasta"), ("?x", "livesIn", "Italy")])),
  ("union",     DS_paper, ["?x"],
     ("union", ("bgp", [("?x", "likes", "pasta")]), ("bgp", [("?x", "livesIn", "Italy")]))),
  ("minus",     DS_paper, ["?x"],
     ("minus", ("bgp", [("?x", "likes", "pasta")]), ("bgp", [("?x", "livesIn", "Italy")]))),
  ("optional",  DS_paper, ["?x", "?c"],
     ("optional", ("bgp", [("?x", "likes", "pasta")]), ("bgp", [("?x", "livesIn", "?c")]))),
  ("chain",     DS_chain, ["?x", "?z"],
     ("bgp", [("?x", "p", "?y"), ("?y", "q", "?z")])),
  ("union2",    DS_chain, ["?x"],
     ("union", ("bgp", [("?x", "p", "?y"), ("?y", "q", "?z")]), ("bgp", [("?x", "r", "?e")]))),
  ("minus2",    DS_chain, ["?x", "?y"],
     ("minus", ("bgp", [("?x", "p", "?y")]), ("bgp", [("?y", "q", "?z")]))),
  ("optional2", DS_chain, ["?x", "?y", "?z"],
     ("optional", ("bgp", [("?x", "p", "?y")]), ("bgp", [("?y", "q", "?z")]))),
  ("opt_disjoint", DS_chain, ["?x", "?y", "?a", "?e"],   # OPTIONAL operands share NO variable
     ("optional", ("bgp", [("?x", "p", "?y")]), ("bgp", [("?a", "r", "?e")]))),
  ("minus_union", DS_chain, ["?x"],
     ("minus", ("bgp", [("?x", "p", "?y")]),
               ("union", ("bgp", [("?y", "q", "?z")]), ("bgp", [("?y", "r", "?w")])))),
  ("share_union", DS_share, ["?a", "?d"],
     ("union", ("bgp", [("?a", "p", "?b"), ("?b", "q", "?d")]),
               ("bgp", [("?a", "p", "?b"), ("?b", "r", "?d")]))),
  ("selfjoin",  DS_self, ["?d"],
     ("bgp", [("?x", "p", "?d"), ("?y", "p", "?d")])),
  # --- property paths ---
  ("path_plus_cyc", DS_cyc, ["?x", "?y"],           # p+ on a cyclic graph (all pairs)
     ("path", "?x", ("plus", ("edge", "p")), "?y")),
  ("path_star_cyc", DS_cyc, ["?x", "?y"],           # p* = p+ (+) zero-length
     ("path", "?x", ("star", ("edge", "p")), "?y")),
  ("path_bound_src", DS_cyc, ["?y"],                # bound source: A p+ ?y
     ("path", "A", ("plus", ("edge", "p")), "?y")),
  ("path_seq",   DS_pth, ["?x", "?z"],              # p/q  (X ->Y ->Z)
     ("path", "?x", ("seq", ("edge", "p"), ("edge", "q")), "?z")),
  ("path_alt",   DS_pth, ["?x", "?z"],              # (p/q)|r  (both reach X->Z: collapse)
     ("path", "?x", ("alt", ("seq", ("edge", "p"), ("edge", "q")), ("edge", "r")), "?z")),
  ("path_inv",   DS_pth, ["?y", "?x"],              # ^p  (inverse of p)
     ("path", "?y", ("inv", ("edge", "p")), "?x")),
  ("path_opt",   DS_pth, ["?x", "?z"],              # r?  (zero-or-one)
     ("path", "?x", ("opt", ("edge", "r")), "?z")),
  ("path_plus_join", DS_cyc, ["?y"],                # path composed with a join (mixed fragment)
     ("join", ("path", "A", ("plus", ("edge", "p")), "?y"),
              ("bgp", [("?y", "p", "A")]))),
]

def assignments(toks, i):
    return {t: round(0.2 + 0.6 * (((j + i) % 5) / 4.0), 3) for j, t in enumerate(toks)}

grand_ok = grand_tot = 0
for name, data, sel, q in TESTS:
    row_ok = row_tot = 0; fails = []
    for i in range(3):
        P = assignments(list(data), i)
        circ = gates.Circuit()
        table = gamma.eval_q(circ, q, data)
        ok, tot, f = wmc.check(circ, table, sel, q, data, P)
        row_ok += ok; row_tot += tot; fails += f
    grand_ok += row_ok; grand_tot += row_tot
    tag = "OK " if not fails else "FAIL"
    print(f"  [{tag}] {name:12} {row_ok}/{row_tot}")
    for fl in fails[:3]:
        print("        mismatch", fl)

print(f"\nTOTAL: {grand_ok}/{grand_tot} answer-probability checks passed")
