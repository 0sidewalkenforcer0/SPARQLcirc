#!/usr/bin/env python3
"""Diff the explicit pure-compatibility rewrite against the original NPCS JAR."""
import subprocess, sys, re, os, glob

# ORIG + QDIR reference the ORIGINAL NPCS artifact (Asma et al., WWW'24) — NOT
# included in this repo. Set NPCS_ORIG_JAR / NPCS_QDIR to run the consistency diff.
_HERE = os.path.dirname(os.path.abspath(__file__))
ORIG = os.environ.get("NPCS_ORIG_JAR", "")     # e.g. .../jarfiles/ReifySparqlByte.jar
MINE = os.environ.get("NPCS_JAR", os.path.join(os.path.dirname(_HERE), "target", "npcs-rewrite.jar"))
QDIR = os.environ.get("NPCS_QDIR", "")          # e.g. .../NPCS/queries/Basic/watdiv

def run(jar, scheme, query):
    p = subprocess.run(["java","-jar",jar,scheme,"query",query],
                       capture_output=True, timeout=120)
    return p.stdout.decode("utf-8","replace"), p.stderr.decode("utf-8","replace")

def compatibility_scheme(scheme):
    """Map an original NPCS scheme name to this JAR's token-only layout."""
    if scheme in ("Standard", "SPARQL_Star", "NamedGraph"):
        return scheme + "_Pure"
    return scheme

def decode_orig(out):
    m = re.search(r'Byte Array:\s*\[([-0-9,\s]*)\]', out)
    if not m: return None
    return bytes([int(x)&0xff for x in m.group(1).split(',') if x.strip()]).decode("utf-8","replace")

_GENSYM = re.compile(r'\?(?:__npcs\d+_)?(?:fprov\d+|fjoin\d+|funion\d+|fdl\d+|fdr\d+|fdiff\d+|rightunion\d+|rightoptional\d+|foptional\d+|fbind\d+|fgroup(?:concat)?\d+|finalprovennacevariable|f)\b')
_COMM = re.compile(r'CONCAT\("\((⊗|⊕)",(.*?),"\)"\)')

def norm(s):
    """Canonicalize a rewritten query for SEMANTIC-equivalence comparison:
       (1) rename provenance gensyms (?fprovN/?fjoinN/...) by order of first
           appearance, and (2) sort the arguments of the commutative operators
           ⊗ (product) and ⊕ (union-sum) into a canonical marker. The
           non-commutative monus ⊖ is left untouched. Whitespace is removed."""
    if not s:
        return s
    mp = {}
    def ren(m):
        t = m.group(0)
        mp.setdefault(t, '?p%d' % len(mp))
        return mp[t]
    s = re.sub(r'\s+', '', _GENSYM.sub(ren, s))
    def block(m):
        op, body = m.group(1), m.group(2)
        ps = sorted(set(re.findall(r'\?p\d+', body)), key=lambda x: int(x[2:]))
        return '<%s:%s>' % ('PROD' if op == '⊗' else 'USUM', ','.join(ps))
    return _COMM.sub(block, s)

def is_bgp(text):
    return not re.search(r'\b(OPTIONAL|UNION|MINUS|FILTER)\b', text, re.I)

def main():
    schemes = sys.argv[1:] or ["Standard","SPARQL_Star"]
    files = sorted(glob.glob(os.path.join(QDIR,"*","*.sparql")))
    bgp = [f for f in files if is_bgp(open(f).read())]
    print(f"WatDiv Basic: {len(files)} files, {len(bgp)} pure-BGP")
    for scheme in schemes:
        ok=mismatch=err=0; bad=[]
        for f in bgp:
            q = open(f).read()
            try:
                o_raw,o_err = run(ORIG, scheme, q)
                m_raw,m_err = run(MINE, compatibility_scheme(scheme), q)
            except subprocess.TimeoutExpired:
                err+=1; bad.append((f,"TIMEOUT")); continue
            o = decode_orig(o_raw)
            if o is None:
                err+=1; bad.append((f,"orig-no-output: "+o_err.strip()[:80])); continue
            if m_raw.strip()=="" :
                err+=1; bad.append((f,"mine-no-output: "+m_err.strip()[:80])); continue
            if norm(o)==norm(m_raw):
                ok+=1
            else:
                mismatch+=1; bad.append((f, o, m_raw))
        total=len(bgp)
        print(f"\n=== scheme={scheme}: MATCH {ok}/{total}, mismatch {mismatch}, error {err} ===")
        for b in bad[:6]:
            if len(b)==2:
                print(f"  [issue] {os.path.relpath(b[0],QDIR)}: {b[1]}")
            else:
                print(f"  [MISMATCH] {os.path.relpath(b[0],QDIR)}")
                print(f"     orig(norm): {norm(b[1])[:160]}")
                print(f"     mine(norm): {norm(b[2])[:160]}")
        if len(bad)>6: print(f"  ... and {len(bad)-6} more")

if __name__=="__main__":
    main()
