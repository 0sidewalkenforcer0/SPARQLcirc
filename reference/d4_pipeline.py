#!/usr/bin/env python3
"""Exploratory Linux/x86 helper: compile exported CNFs with an environment-selected d4.

This script is deliberately **not** a formal/publication harness: its compiler
path and argv template are ambient environment inputs, it has no frozen-input
identity, and ``results.csv`` is classified accordingly. Formal treewidth
measurements use ``paper/controlled_mechanisms.py`` and its pinned argv/hash.

Compile each exported CNF with d4 to a d-DNNF, record its size,
weighted-model-count that dumped d-DNNF locally, and check against cnf/manifest.json (produced
on any machine by export_cnf.py). This is the compiler-scaling data point that
cannot be produced on Apple Silicon (d4's bundled PATOH partitioner is x86_64-only).

Usage (on a Linux/x86 box with d4 built):
    D4=/path/to/d4  python3 d4_pipeline.py            # d4 v1 (crillab/d4)
    D4=/path/to/d4v2 D4V2=1 python3 d4_pipeline.py     # d4 v2

Outputs results.csv: instance, nvars, d-DNNF nodes/edges, compiled-NNF WMC, expected-WMC,
our OBDD size, match?  -> the d-DNNF-size vs OBDD-size comparison for the figure.

d4 CLIs differ between versions; adjust D4_DDNNF_CMD / D4V2_DDNNF_CMD to match a pinned build.
The compiler is invoked exactly once per CNF; ddnnf_wmc.py performs the subsequent linear evaluation.
"""
import os, re, json, subprocess, csv, sys, shlex
import ddnnf_wmc
from experiment_timeouts import COMPILE_TIMEOUT_S

D4 = os.environ.get("D4", "d4")
V2 = os.environ.get("D4V2")
EVIDENCE_CLASSIFICATION = "exploratory-unfrozen-environment-command-template"

def ddnnf_cmd(cnf, out, d4bin=None):
    """Version-aware compilation command, with a template override for a pinned server build."""
    binary = d4bin or D4
    if V2:  # current d4v2 / ProvSQL registry syntax, forced CNF input
        template = os.environ.get(
            "D4V2_DDNNF_CMD",
            "{d4} -i {cnf} -m ddnnf-compiler --dump-ddnnf {out}",
        )
    else:
        template = os.environ.get("D4_DDNNF_CMD", "{d4} {cnf} -dDNNF -out={out}")
    return shlex.split(template.format(d4=binary, cnf=cnf, out=out))

def nnf_size(path):
    """d4 v1 d-DNNF text format: node lines 'o|a|t|f <id> 0'; arc lines '<from> <to> [lits] 0'.
    (Also handles the classic 'nnf <nodes> <edges> <vars>' header if a build emits it.)"""
    nodes = edges = 0
    with open(path) as f:
        for line in f:
            m = re.match(r"\s*nnf\s+(\d+)\s+(\d+)\s+(\d+)", line)
            if m:
                return int(m.group(1)), int(m.group(2))
            if re.match(r"[oatf]\s", line): nodes += 1
            elif re.match(r"\d", line): edges += 1
    return nodes, edges

def main():
    print("WARNING: d4_pipeline.py emits exploratory, non-publication evidence",
          file=sys.stderr)
    man = json.load(open("cnf/manifest.json"))
    rows = []
    for e in man:
        cnf = os.path.join("cnf", e["cnf"]); nnf = cnf + ".nnf"
        try:
            subprocess.run(ddnnf_cmd(cnf, nnf), check=True, capture_output=True,
                           timeout=COMPILE_TIMEOUT_S)
            ev = ddnnf_wmc.evaluate_tseitin_file(
                nnf, ddnnf_wmc.weights_from_dimacs(cnf)
            )
            nodes, edges, d4wmc = ev.nodes, ev.edges, ev.probability
        except Exception as ex:
            print(f"[{e['instance']}] d4 failed: {ex}"); nodes = edges = d4wmc = None
        ok = d4wmc is not None and abs(d4wmc - e["expected_wmc"]) < 1e-6
        rows.append({
            **e,
            "evidence_classification": EVIDENCE_CLASSIFICATION,
            "ddnnf_nodes": nodes,
            "ddnnf_edges": edges,
            "d4_wmc": d4wmc,
            "match": ok,
        })
        print(f"[{e['instance']}] d-DNNF nodes={nodes} edges={edges}  NNF-WMC={d4wmc} "
              f"expected={e['expected_wmc']} OBDD={e['obdd_size']}  {'OK' if ok else 'CHECK'}")
    with open("results.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    print("\nwrote results.csv  (compare ddnnf_nodes vs obdd_size for the scaling figure)")
    return 0 if rows and all(r["match"] for r in rows) else 1

if __name__ == "__main__":
    if not any(os.path.exists(p) for p in [D4, "/usr/bin/d4"]) and D4 == "d4":
        subprocess.run(["which", "d4"], capture_output=True).returncode  # informative only
    sys.exit(main())
