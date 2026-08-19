"""Shared parser: engine circuit N-Triples -> compile-ready gate dict + TERM-AWARE answer bindings.

ONE place that turns the emitted RDF circuit into
  (a) `circ`     : {node: ('leaf',tok)|('times',(kids))|('plus',(kids))|('minus',(m,s))}  (compile_bdd format)
  (b) `answers`  : set of answer-gate IRIs
  (c) `bindings` : {gate: {var: canonical-RDF-term}}   recovered from either legacy
                   c:binding / c:var / c:val nodes or native direct-binding predicates
                   (NOT the lossy readable c:answer string).

Every verification / experiment consumer should call `parse()` and identify answers by
`answer_key(bindings[gate])` (or the gate IRI), so answer identity is term-aware everywhere:
an IRI vs a same-lexical literal, differing datatype / language tag, or bound-vs-unbound never
collapse. `canon_rdflib()` gives the matching key for a PWE oracle's rdflib term.
"""
SK = "urn:sk:"                                                     # npcs.rewrite.Skolem.NS
RS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
C = "urn:circuit:"
BIND_PREFIX = C + "bind:"
UNBOUND_PREFIX = C + "unbound:"
ANSWER_SCHEMA_PREFIX = "vars:"
XSD_STRING = "http://www.w3.org/2001/XMLSchema#string"
RDF_LANGSTRING = RS + "langString"
US = "\x1f"                                                        # unit separator (never in our terms)
_NT_ESC = {"t": "\t", "b": "\b", "n": "\n", "r": "\r", "f": "\f", '"': '"', "'": "'", "\\": "\\", "/": "/"}


class CircuitFormatError(ValueError):
    """The RDF graph does not satisfy the provenance-circuit interchange contract."""


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


def _iri_unescape(value):
    """Decode the UCHAR escapes allowed in an N-Triples IRIREF."""
    if "\\" not in value:
        return value
    out = []
    index = 0
    while index < len(value):
        if value[index] != "\\":
            out.append(value[index])
            index += 1
            continue
        kind = value[index + 1] if index + 1 < len(value) else ""
        digits = 4 if kind == "u" else 8 if kind == "U" else 0
        if not digits or index + 2 + digits > len(value):
            raise CircuitFormatError(f"invalid IRIREF escape in {value!r}")
        try:
            codepoint = int(value[index + 2:index + 2 + digits], 16)
        except ValueError as exc:
            raise CircuitFormatError(f"invalid IRIREF escape in {value!r}") from exc
        if codepoint > 0x10FFFF or 0xD800 <= codepoint <= 0xDFFF:
            raise CircuitFormatError(f"invalid Unicode scalar in IRIREF {value!r}")
        out.append(chr(codepoint))
        index += 2 + digits
    return "".join(out)


def _resource(token, label):
    token = token.strip()
    if token.startswith("<") and token.endswith(">"):
        return _iri_unescape(token[1:-1])
    if token.startswith("_:") and len(token) > 2:
        return token
    raise CircuitFormatError(f"{label} must be an IRI or blank node: {token!r}")


def _iri(token, label):
    token = token.strip()
    if token.startswith("<") and token.endswith(">"):
        return _iri_unescape(token[1:-1])
    raise CircuitFormatError(f"{label} must be an IRI: {token!r}")


def _plain_literal(token, label):
    token = token.strip()
    if not token.startswith('"'):
        raise CircuitFormatError(f"{label} must be a plain literal: {token!r}")
    end = token.rfind('"')
    if end == 0 or token[end + 1:]:
        raise CircuitFormatError(f"{label} must be a plain literal: {token!r}")
    return _nt_unescape(token[1:end])


def _collect(mapping, key, value):
    mapping.setdefault(key, set()).add(value)


def _single(mapping, key, label, required=False):
    values = sorted(mapping.get(key, ()))
    if len(values) > 1:
        raise CircuitFormatError(f"{label} has conflicting values: {values}")
    if required and not values:
        raise CircuitFormatError(f"{label} is missing")
    return values[0] if values else None


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
        iri = _iri_unescape(tok[1:-1])
        label = unskolemize(iri)
        return ("b" + US + label) if label is not None else ("i" + US + iri)
    if tok.startswith("_:"):
        return "b" + US + tok[2:]
    if tok.startswith('"'):
        i = tok.rindex('"'); lex, suf = _nt_unescape(tok[1:i]), tok[i + 1:]   # decode escapes -> real lexical value
        if suf.startswith("^^<") and suf.endswith(">"):
            return "l" + US + lex + US + _iri_unescape(suf[3:-1]) + US
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
    ans_gates, g_bind, b_var, b_val, direct_bind, answer_schema = set(), {}, {}, {}, {}, {}
    for line in lines:
        line = line.strip()
        if not line.endswith(" ."):
            continue
        try:
            raw_s, raw_p, o = line[:-2].split(None, 2)
        except ValueError as exc:
            raise CircuitFormatError(f"malformed N-Triples statement: {line!r}") from exc
        s = _resource(raw_s, "statement subject")
        p = _iri(raw_p, "statement predicate")
        o = o.strip()
        if p == RS + "type": _collect(typ, s, _iri(o, "rdf:type object"))
        elif p == C + "feeds": feeds.setdefault(_resource(o, "c:feeds object"), set()).add(s)
        elif p == C + "in": tin.setdefault(s, set()).add(_resource(o, "c:in object"))
        elif p == C + "minuend": _collect(minu, s, _resource(o, "c:minuend object"))
        elif p == C + "subtrahend": _collect(subt, s, _resource(o, "c:subtrahend object"))
        elif p == C + "answer": ans_gates.add(s)
        elif p == C + "answerRoot":
            ans_gates.add(s)
            if o.startswith('"' + ANSWER_SCHEMA_PREFIX):
                _collect(answer_schema, s, _plain_literal(o, "c:answerRoot schema"))
        elif p == C + "binding": g_bind.setdefault(s, set()).add(_resource(o, "c:binding object"))
        elif p == C + "var": _collect(b_var, s, _plain_literal(o, "c:var object"))
        elif p == C + "val": _collect(b_val, s, o)                         # RAW N-Triples term token
        elif p.startswith(BIND_PREFIX):
            variable = _decode_variable(p[len(BIND_PREFIX):], p)
            _collect(direct_bind, s, (variable, o))
            ans_gates.add(s)
        elif p.startswith(UNBOUND_PREFIX):
            variable = _decode_variable(p[len(UNBOUND_PREFIX):], p)
            _collect(direct_bind, s, (variable, None))
            ans_gates.add(s)
    # A gate carrying c:binding IS an answer gate — do NOT rely on the c:answer DEBUG label, which can be
    # dropped when a projected var is unbound in a UNION/OPTIONAL branch (STR(?unbound) -> label unbound).
    ans_gates |= set(g_bind)
    inferred = {}
    for node in tin:
        _collect(inferred, node, C + "Times")
    for node in set(minu) | set(subt):
        _collect(inferred, node, C + "Minus")
    for node in set(feeds) | ans_gates:
        _collect(inferred, node, C + "Plus")
    types = {}
    for node in sorted(set(typ) | set(inferred)):
        candidates = set(typ.get(node, ())) | set(inferred.get(node, ()))
        if len(candidates) != 1:
            raise CircuitFormatError(f"gate {node} has conflicting types: {sorted(candidates)}")
        types[node] = next(iter(candidates))
    minus_nodes = {node for node, gate_type in types.items() if gate_type.endswith("Minus")}
    stray_minus_fields = (set(minu) | set(subt)) - minus_nodes
    if stray_minus_fields:
        raise CircuitFormatError(
            f"Minus operands occur on non-Minus nodes: {sorted(stray_minus_fields)}"
        )
    circ = {}
    for n, t in types.items():
        if t.endswith("Times"): circ[n] = ("times", tuple(sorted(tin.get(n, ()))))
        elif t.endswith("Plus"): circ[n] = ("plus", tuple(sorted(feeds.get(n, ()))))
        elif t.endswith("Minus"):
            circ[n] = ("minus", (
                _single(minu, n, f"c:minuend of {n}", required=True),
                _single(subt, n, f"c:subtrahend of {n}", required=True),
            ))
    ref = set()
    for op, pl in circ.values():
        ref |= set(pl) if op in ("times", "plus") else {pl[0], pl[1]}
    for r in ref:
        circ.setdefault(r, ("leaf", r))
    bindings = {}
    for g in sorted(ans_gates):
        d = {}
        seen = set()
        schema = _single(answer_schema, g, f"c:answerRoot schema of {g}")
        if schema is not None:
            for v in _decode_answer_schema(schema):
                d[v] = "u"
        for b in sorted(g_bind.get(g, ())):
            v = _single(b_var, b, f"c:var of binding {b}", required=True)
            if v in seen:
                raise CircuitFormatError(f"answer gate {g} has multiple bindings for ?{v}")
            value = _single(b_val, b, f"c:val of binding {b}")
            d[v] = canon_term(value)                                     # missing c:val -> 'u' (unbound)
            seen.add(v)
        for v, value in sorted(direct_bind.get(g, ()), key=lambda item: item[0]):
            if v in seen:
                raise CircuitFormatError(f"answer gate {g} has multiple bindings for ?{v}")
            if schema is not None and v not in d:
                raise CircuitFormatError(f"answer gate {g} binds undeclared variable ?{v}")
            d[v] = canon_term(value)
            seen.add(v)
        bindings[g] = d
    return circ, ans_gates, bindings


def _decode_variable(encoded, predicate):
    """Inverse of the UTF-8 hex variable-name predicate encoding."""
    if not encoded or len(encoded) % 2:
        raise CircuitFormatError(f"invalid binding predicate: {predicate!r}")
    try:
        return bytes.fromhex(encoded).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise CircuitFormatError(f"invalid binding predicate: {predicate!r}") from exc


def _decode_answer_schema(schema):
    """Decode `vars:<utf8-hex>[,<utf8-hex>...]` from an answer-root object."""
    if not schema.startswith(ANSWER_SCHEMA_PREFIX):
        raise CircuitFormatError(f"invalid answer schema: {schema!r}")
    encoded_variables = schema[len(ANSWER_SCHEMA_PREFIX):]
    if not encoded_variables:
        return ()
    variables = tuple(
        _decode_variable(encoded, C + "answerRoot")
        for encoded in encoded_variables.split(",")
    )
    if len(set(variables)) != len(variables):
        raise CircuitFormatError(f"duplicate variable in answer schema: {schema!r}")
    return variables


def answer_probs(nt, P, prob_fn):
    """Convenience: {term-aware answer_key: probability}. prob_fn(circ, gate, P) may return a float or
    a (prob, size) tuple (compile_bdd.probability returns the latter)."""
    circ, answers, bindings = parse(nt)
    out = {}
    for g in answers:
        r = prob_fn(circ, g, P)
        out[answer_key(bindings[g])] = round(r[0] if isinstance(r, tuple) else r, 10)
    return out
