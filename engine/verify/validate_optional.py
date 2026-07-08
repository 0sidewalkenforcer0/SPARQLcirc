#!/usr/bin/env python3
"""Well-formedness check: every WatDiv OPTIONAL query, rewritten by the clean
JAR, must re-parse as valid SPARQL (both schemes)."""
import subprocess, glob, os, tempfile
_HERE=os.path.dirname(os.path.abspath(__file__))
MINE=os.environ.get("NPCS_JAR", os.path.join(os.path.dirname(_HERE), "target", "npcs-rewrite.jar"))
QDIR=os.environ.get("NPCS_QDIR", "")  # original NPCS WatDiv queries (Asma et al. WWW'24; not included)
opt=[f for f in sorted(glob.glob(os.path.join(QDIR,"*","*.sparql")))
     if 'OPTIONAL' in open(f).read().upper() and open(f).read().strip()]
print(f"{len(opt)} OPTIONAL query files")
for scheme in ("Standard","SPARQL_Star"):
    ok=fail=err=0; bad=[]
    for f in opt:
        q=open(f).read()
        r=subprocess.run(["java","-jar",MINE,scheme,"query",q],capture_output=True,timeout=120)
        if r.returncode!=0 or not r.stdout.strip():
            err+=1; bad.append((f,"rewrite-error: "+r.stderr.decode()[:80])); continue
        with tempfile.NamedTemporaryFile("wb",suffix=".rq",delete=False) as tf:
            tf.write(r.stdout); tp=tf.name
        c=subprocess.run(["java","-jar",MINE,"parsecheck","path",tp],capture_output=True,timeout=120)
        os.unlink(tp)
        if c.stdout.decode().startswith("PARSE_OK"): ok+=1
        else: fail+=1; bad.append((f,c.stdout.decode().strip()[:120]))
    print(f"=== {scheme}: PARSE_OK {ok}/{len(opt)}, parse-fail {fail}, rewrite-err {err} ===")
    for b in bad[:8]: print("   ",os.path.relpath(b[0],QDIR),"->",b[1])
