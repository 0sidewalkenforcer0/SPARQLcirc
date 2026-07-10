#!/usr/bin/env python3
"""Linux/x86 step: compile each exported CNF with d4 to a d-DNNF, record its size,
get d4's weighted model count, and check both against cnf/manifest.json (produced
on any machine by export_cnf.py). This is the compiler-scaling data point that
cannot be produced on Apple Silicon (d4's bundled PATOH partitioner is x86_64-only).

Usage (on a Linux/x86 box with d4 built):
    D4=/path/to/d4  python3 d4_pipeline.py            # d4 v1 (crillab/d4)
    D4=/path/to/d4v2 D4V2=1 python3 d4_pipeline.py     # d4 v2

Outputs results.csv: instance, nvars, d-DNNF nodes/edges, d4-WMC, expected-WMC,
our OBDD size, match?  -> the d-DNNF-size vs OBDD-size comparison for the figure.

d4 CLIs differ between versions; adjust D4_DDNNF_CMD / D4_WMC_CMD below to match
your build. The defaults cover the two common invocations.
"""
import os, re, json, subprocess, csv, sys

D4 = os.environ.get("D4", "d4")
V2 = os.environ.get("D4V2")

def ddnnf_cmd(cnf, out):
    if V2:  # d4v2: crillab/d4v2
        return [D4, "--input", cnf, "--method", "ddnnf", "--dump-ddnnf", out]
    return [D4, cnf, "-dDNNF", f"-out={out}"]           # d4 v1 (crillab/d4): positional input

def wmc_cmd(cnf, wfile):
    if V2:
        return [D4, "--input", cnf, "--method", "wmc"]
    # d4 v1 does WEIGHTED counting via -mc + an external -wFile of '<lit> <weight>' pairs;
    # the 'c p weight ... 0' lines inside the CNF are comments d4 ignores.
    return [D4, cnf, "-mc", f"-wFile={wfile}"]

def write_weights(cnf, wfile):
    """Extract the CNF's 'c p weight <lit> <w> 0' comment lines into a d4 -wFile
    ('<lit> <weight>' pairs). Literals not listed default to weight 1 in d4."""
    with open(cnf) as f, open(wfile, "w") as g:
        for line in f:
            p = line.split()
            if len(p) == 6 and p[:3] == ["c", "p", "weight"]:   # 'c p weight <lit> <w> 0'
                g.write(f"{p[3]} {p[4]}\n")

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

def parse_wmc(stdout):
    # d4 prints the count on a line like 's <value>' or 'c s exact ... <value>'
    for pat in [r"^s\s+([0-9.eE+\-]+)", r"exact\s+.*?\s([0-9.eE+\-]+)\s*$", r"([0-9]*\.[0-9eE+\-]+)\s*$"]:
        for line in reversed(stdout.splitlines()):
            m = re.search(pat, line.strip())
            if m:
                try: return float(m.group(1))
                except ValueError: pass
    return None

def main():
    man = json.load(open("cnf/manifest.json"))
    rows = []
    for e in man:
        cnf = os.path.join("cnf", e["cnf"]); nnf = cnf + ".nnf"; wf = cnf + ".w"
        write_weights(cnf, wf)
        try:
            subprocess.run(ddnnf_cmd(cnf, nnf), check=True, capture_output=True, timeout=600)
            nodes, edges = nnf_size(nnf)
            wout = subprocess.run(wmc_cmd(cnf, wf), check=True, capture_output=True, text=True, timeout=600)
            d4wmc = parse_wmc(wout.stdout)
        except Exception as ex:
            print(f"[{e['instance']}] d4 failed: {ex}"); nodes = edges = d4wmc = None
        ok = d4wmc is not None and abs(d4wmc - e["expected_wmc"]) < 1e-6
        rows.append({**e, "ddnnf_nodes": nodes, "ddnnf_edges": edges, "d4_wmc": d4wmc, "match": ok})
        print(f"[{e['instance']}] d-DNNF nodes={nodes} edges={edges}  d4-WMC={d4wmc} "
              f"expected={e['expected_wmc']} OBDD={e['obdd_size']}  {'OK' if ok else 'CHECK'}")
    with open("results.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    print("\nwrote results.csv  (compare ddnnf_nodes vs obdd_size for the scaling figure)")

if __name__ == "__main__":
    if not any(os.path.exists(p) for p in [D4, "/usr/bin/d4"]) and D4 == "d4":
        subprocess.run(["which", "d4"], capture_output=True).returncode  # informative only
    main()
