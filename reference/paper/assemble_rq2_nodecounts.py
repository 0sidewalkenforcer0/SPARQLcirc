#!/usr/bin/env python3
"""Assemble RQ2 compactness deliverables (fig:compact) from the campaign CSVs.

flat/NPCS  : nodecount_flat_10m.csv (Q2, 10M) + rq3/graphdb-100m/graphdb_100m_assembled.csv (Q1, 100M)
factored   : nodecount_factored_10m.csv (Q3, 10M) + nodecount_factored_100m_{fls,c}.csv (Q4, 100M)
Filters to the figure's classes C,F,L,O,S. Node columns are already v9-instrumented; we only
select/concat rows (schema is identical across CSVs). Deterministic: single run per cell.
"""
import csv, os, sys, glob

ART = "/mnt/nfs/home/ac145595/workspace/rq3-artifacts"
HERE = os.path.dirname(os.path.abspath(__file__))
CLASSES = {"C", "F", "L", "O", "S"}

def read(path):
    return list(csv.DictReader(open(path))) if os.path.exists(path) else []

def write(path, header, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header); w.writeheader()
        for r in rows: w.writerow(r)
    ok = sum(1 for r in rows if r.get("status") == "ok")
    print(f"  wrote {path}  ({len(rows)} rows, {ok} ok)")

def assemble_flat():
    r10 = read(f"{ART}/nodecount_flat_10m.csv")
    r100 = read(f"{HERE}/rq3/graphdb-100m/graphdb_100m_assembled.csv")
    if not r10 or not r100:
        print("  flat: missing inputs, skip"); return None
    header = list(r10[0].keys())
    keep = lambda r: r["class"] in CLASSES and r["method"] in ("N", "C")
    rows = [r for r in r10 if keep(r)] + [r for r in r100 if keep(r)]
    out = f"{HERE}/nodecount_flat_10m_100m.csv"; write(out, header, rows); return out

def assemble_factored():
    r10 = read(f"{ART}/nodecount_factored_10m.csv")
    r100 = read(f"{ART}/nodecount_factored_100m_fls.csv") + read(f"{ART}/nodecount_factored_100m_c.csv")
    if not r10:
        print("  factored: missing 10M input, skip"); return None
    header = list(r10[0].keys())
    keep = lambda r: r["class"] in CLASSES and r["method"] == "C"
    rows = [r for r in r10 if keep(r)] + [r for r in r100 if keep(r)]
    if not r100: print("  factored: 100M not ready yet (Q4 pending) — writing 10M-only for now")
    out = f"{HERE}/nodecount_factored_10m_100m.csv"; write(out, header, rows); return out

if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    print("Assembling RQ2 node-count deliverables:")
    if which in ("all", "flat"): assemble_flat()
    if which in ("all", "factored"): assemble_factored()
