"""circuit_io literal-canonicalization regression (the P2 fix).

canon_term() used to keep the RAW N-Triples spelling of a literal's lexical value; a literal containing
\\" \\\\ \\n or \\uXXXX therefore canonicalized DIFFERENTLY from canon_rdflib() (rdflib stores the decoded
value), so answer recovery was not lossless for escaped literals. This checks that:
  1. the escapes decode to the intended value (independent of rdflib);
  2. canon_term(raw N-Triples token) == canon_rdflib(the corresponding rdflib term)  [if rdflib present];
  3. distinct escaped literals stay distinct, and plain ASCII literals are unaffected (no regression).
"""
import sys
import circuit_io as cio

# (raw N-Triples object token, expected decoded lexical value)
CASES = [
    (r'"plain"',                       "plain"),
    (r'"a\"b"',                        'a"b'),                 # escaped quote
    (r'"a\\b"',                        "a\\b"),                # escaped backslash
    (r'"line1\nline2"',                "line1\nline2"),        # newline
    (r'"tab\there"',                   "tab\there"),           # tab
    (f'"caf{chr(92)}u00e9"',           "café"),                # é escape -> é (chr(92)=backslash so the
                                                               #   source isn't mangled); exercises the \u branch
    ('"literal-é"',                    "literal-é"),           # actual Unicode char, no escape -> passthrough
    (r'"smiley\U0001F600"',            "smiley\U0001F600"),    # \UXXXXXXXX escape (emoji)
    (r'"mix \"q\" and \\ and \n"',     'mix "q" and \\ and \n'),
    (r'"typed\"q"^^<http://example/dt>', 'typed"q'),           # escaped quote + datatype
    (r'"lang\nbreak"@EN',              "lang\nbreak"),         # escaped + language tag (lang lowercased)
]

def main():
    ok = True
    # (1) decode correctness (no rdflib needed).
    for tok, want in CASES:
        got = cio._nt_unescape(tok[1:tok.rindex('"')])
        good = got == want
        ok &= good
        print(f"[decode] {tok:34} -> {want!r:24} {'OK' if good else 'FAIL got=' + repr(got)}")

    # (2) distinctness: every case produces a distinct canonical key; plain != escaped-quote etc.
    keys = [cio.canon_term(tok) for tok, _ in CASES]
    distinct = len(set(keys)) == len(keys)
    ok &= distinct
    print(f"\n[distinct] {len(set(keys))}/{len(keys)} canonical keys distinct  {'OK' if distinct else 'FAIL'}")
    # a raw-vs-decoded confusion would keep the backslash; assert the decoded quote is present:
    c_esc = cio.canon_term(r'"a\"b"')                 # lexical a"b
    sane = ('a"b' in c_esc) and ("\\" not in c_esc.split(cio.US)[1])
    ok &= sane
    print(f"[sanity]  canon('a\\\"b') carries decoded lexical 'a\"b'  {'OK' if sane else 'FAIL'}")

    # (3) match rdflib (the PWE-oracle side), if available.
    try:
        import rdflib
        from rdflib.plugins.parser.ntriples import unquote  # noqa: F401  (presence check)
    except Exception:
        print("\n[rdflib] not available locally — decode+distinctness checks stand; "
              "server suite covers canon_term == canon_rdflib.")
        print("\nALL OK" if ok else "\nFAILURES"); sys.exit(0 if ok else 1)

    print()
    for tok, want in CASES:
        # build the matching rdflib term and compare canonical keys
        i = tok.rindex('"'); suf = tok[i + 1:]
        if suf.startswith("^^<"):
            term = rdflib.Literal(want, datatype=rdflib.URIRef(suf[3:-1]))
        elif suf.startswith("@"):
            term = rdflib.Literal(want, lang=suf[1:])
        else:
            term = rdflib.Literal(want)
        a, b = cio.canon_term(tok), cio.canon_rdflib(term)
        good = a == b
        ok &= good
        print(f"[rdflib] {tok:34} canon_term==canon_rdflib? {'OK' if good else 'FAIL'}")
        if not good:
            print(f"           term={a!r}\n           rdfl={b!r}")

    print("\nALL OK" if ok else "\nFAILURES")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
