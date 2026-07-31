"""Shared parser: engine circuit N-Triples -> compile-ready gate dict + TERM-AWARE answer bindings.

ONE place that turns the emitted RDF circuit into
  (a) `circ`     : {node: ('leaf',tok)|('times',(kids))|('plus',(kids))|('minus',(m,s))}  (compile_bdd format)
  (b) `answers`  : set of answer-gate IRIs
  (c) `bindings` : {gate: {var: canonical-RDF-term}}   recovered from the structured
                   c:binding / c:var / c:val nodes (NOT the lossy readable c:answer string).

Every verification / experiment consumer should call `parse()` and identify answers by
`answer_key(bindings[gate])` (or the gate IRI), so answer identity is term-aware everywhere:
an IRI vs a same-lexical literal, differing datatype / language tag, or bound-vs-unbound never
collapse. `canon_rdflib()` gives the matching key for a PWE oracle's rdflib term.
"""
SK = "urn:sk:"                                                     # npcs.rewrite.Skolem.NS
RS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
C = "urn:circuit:"
XSD_STRING = "http://www.w3.org/2001/XMLSchema#string"
RDF_LANGSTRING = RS + "langString"
US = "\x1f"                                                        # unit separator (never in our terms)
_NT_ESC = {"t": "\t", "b": "\b", "n": "\n", "r": "\r", "f": "\f", '"': '"', "'": "'", "\\": "\\", "/": "/"}


def _nt_unescape(s):
    """Decode N-Triples/Turtle string escapes (\\t \\n \\r \\" \\\\ \\uXXXX \\UXXXXXXXX ...) to the actual
    lexical value, so a literal's canonical key matches rdflib's (which stores the decoded value)."""
    if "\\" not in s:
        return s
    out, i, n = [], 0, len(s)
    while i < n:
        c = s[i]
        if c != "\\":
            out.append(c); i += 1; continue
        nx = s[i + 1] if i + 1 < n else ""
        if nx == "u":
            out.append(chr(int(s[i + 2:i + 6], 16))); i += 6
        elif nx == "U":
            out.append(chr(int(s[i + 2:i + 10], 16))); i += 10
        elif nx in _NT_ESC:
            out.append(_NT_ESC[nx]); i += 2
        else:
            out.append(nx); i += 2                                  # unknown escape: keep the escaped char
    return "".join(out)


def unskolemize(iri):
    """sk^-1. §4.2 has the client apply it to projected answer terms, so an answer that bound a
    blank node is reported as one rather than as the IRI the graph was loaded under. sk is
    `urn:sk:<hex of the UTF-8 label>` (npcs.rewrite.Skolem), which is its own inverse — no map file
    on either side. Returns None when the IRI is not in sk's image."""
    if not iri.startswith(SK):
        return None
    encoded = iri[len(SK):]
    if not encoded or len(encoded) % 2:
        return None
    try:
        return bytes.fromhex(encoded).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


def canon_term(tok):
    """Term-aware canonical key for a raw N-Triples object token (or None -> unbound)."""
    if tok is None:
        return "u"
    tok = tok.strip()
    if tok.startswith("<") and tok.endswith(">"):
        iri = tok[1:-1]
        label = unskolemize(iri)
        return ("b" + US + label) if label is not None else ("i" + US + iri)
    if tok.startswith("_:"):
        return "b" + US + tok[2:]
    if tok.startswith('"'):
        i = tok.rindex('"'); lex, suf = _nt_unescape(tok[1:i]), tok[i + 1:]   # decode escapes -> real lexical value
        if suf.startswith("^^<") and suf.endswith(">"):
            return "l" + US + lex + US + suf[3:-1] + US
        if suf.startswith("@"):
            return "l" + US + lex + US + RDF_LANGSTRING + US + suf[1:].lower()
        return "l" + US + lex + US + XSD_STRING + US
    return "?" + US + tok


def canon_rdflib(term):
    """Term-aware canonical key for an rdflib term (PWE oracle side) -- matches canon_term()."""
    import rdflib
    if term is None:
        return "u"
    if isinstance(term, rdflib.URIRef):
        return "i" + US + str(term)
    if isinstance(term, rdflib.BNode):
        return "b" + US + str(term)
    lang = (term.language or "").lower()
    dt = str(term.datatype) if term.datatype else (RDF_LANGSTRING if lang else XSD_STRING)
    return "l" + US + str(term) + US + dt + US + lang


def canon_iri(iri):
    """Convenience: term-aware key for a bare IRI string (oracles whose values are IRI strings)."""
    return "i" + US + iri


def answer_key(binding):
    """Order-independent term-aware answer key from {var: canon-term-or-'u'} ('A' if no vars)."""
    return "A|" + "|".join(sorted(f"{v}={c}" for v, c in binding.items())) if binding else "A"


def parse(nt):
    """nt: a string or iterable of N-Triples lines. Returns (circ, answers, bindings)."""
    lines = nt.splitlines() if isinstance(nt, str) else nt
    typ, feeds, tin, minu, subt = {}, {}, {}, {}, {}
    ans_gates, g_bind, b_var, b_val = set(), {}, {}, {}
    for line in lines:
        line = line.strip()
        if not line.endswith(" ."):
            continue
        s, p, o = line[:-2].split(None, 2)
        s, p, o = s.strip("<>"), p.strip("<>"), o.strip()
        oi = o.strip("<>")
        if p == RS + "type": typ[s] = oi
        elif p == C + "feeds": feeds.setdefault(oi, set()).add(s)          # s feeds o
        elif p == C + "in": tin.setdefault(s, set()).add(oi)
        elif p == C + "minuend": minu[s] = oi
        elif p == C + "subtrahend": subt[s] = oi
        elif p == C + "answer": ans_gates.add(s)
        elif p == C + "binding": g_bind.setdefault(s, set()).add(oi)
        elif p == C + "var": b_var[s] = o.strip('"')
        elif p == C + "val": b_val[s] = o                                 # RAW N-Triples term token
    # A gate carrying c:binding IS an answer gate — do NOT rely on the c:answer DEBUG label, which can be
    # dropped when a projected var is unbound in a UNION/OPTIONAL branch (STR(?unbound) -> label unbound).
    ans_gates |= set(g_bind)
    circ = {}
    for n, t in typ.items():
        if t.endswith("Times"): circ[n] = ("times", tuple(sorted(tin.get(n, ()))))
        elif t.endswith("Plus"): circ[n] = ("plus", tuple(sorted(feeds.get(n, ()))))
        elif t.endswith("Minus"): circ[n] = ("minus", (minu.get(n), subt.get(n)))
    ref = set()
    for op, pl in circ.values():
        ref |= set(pl) if op in ("times", "plus") else {pl[0], pl[1]}
    for r in ref:
        circ.setdefault(r, ("leaf", r))
    bindings = {}
    for g in ans_gates:
        d = {}
        for b in g_bind.get(g, ()):
            v = b_var.get(b)
            if v is not None:
                d[v] = canon_term(b_val.get(b))                          # missing c:val -> 'u' (unbound)
        bindings[g] = d
    return circ, ans_gates, bindings


def answer_probs(nt, P, prob_fn):
    """Convenience: {term-aware answer_key: probability}. prob_fn(circ, gate, P) may return a float or
    a (prob, size) tuple (compile_bdd.probability returns the latter)."""
    circ, answers, bindings = parse(nt)
    out = {}
    for g in answers:
        r = prob_fn(circ, g, P)
        out[answer_key(bindings[g])] = round(r[0] if isinstance(r, tuple) else r, 10)
    return out
