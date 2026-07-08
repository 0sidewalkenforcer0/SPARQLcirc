"""Engine-native factored construction: variable elimination run as a sequence of
SPARQL INSERT passes on an unmodified engine (rdflib here; same SPARQL runs on
GraphDB/RDF4J). Generalizes the flat engine-native gamma (npcs.circuit) to the
multi-pass plan.

Scope: BGP (the case where factoring beats the flat |data|^#patterns blowup).
MINUS/OPTIONAL keep the flat engine-native plan in npcs.circuit.CircuitRewriter.

Message relations are materialized as RDF and joined by later passes:
  ?row <urn:m:msg> "<MID>" ; <urn:m:g> <gate> ; <urn:mv:VAR> <val> ...
Circuit gates use the usual vocab urn:circuit: {Times,Plus,in,feeds,answer}.
Row and gate IRIs are content-addressed (SHA256 in SPARQL) so equal
bindings/gates are shared and marginalization groups correctly.

Left-deep with early marginalization: after joining pattern i, drop every
variable not needed by a later pattern or the output. For a chain the running
message has <=2 variables, so it stays polynomial.
"""
RS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#subject"
RP = "http://www.w3.org/1999/02/22-rdf-syntax-ns#predicate"
RO = "http://www.w3.org/1999/02/22-rdf-syntax-ns#object"
MSG, MV, CC = "urn:m:", "urn:mv:", "urn:circuit:"


def _t(x):
    return ("?" + x[1:]) if x.startswith("?") else "<" + x + ">"

def _vars(pat):
    out = []
    for x in pat:
        if x.startswith("?") and x[1:] not in out:
            out.append(x[1:])
    return out

def _key(tag, vs):
    s = 'CONCAT("' + tag + '"'
    for v in vs:
        s += f', "|{v}=", STR(?{v})'
    return s + ")"

def _row(rowvar, mid, gvar, vs):
    s = f'{rowvar} <{MSG}msg> "{mid}" ; <{MSG}g> {gvar}'
    for v in vs:
        s += f' ; <{MV}{v}> ?{v}'
    return s + " ."


def _base(pat, vs, mid):
    s, p, o = pat
    reif = f"?tok <{RS}> {_t(s)} . ?tok <{RP}> {_t(p)} . ?tok <{RO}> {_t(o)} ."
    return (f"INSERT {{ ?bg a <{CC}Plus> . ?tok <{CC}feeds> ?bg . {_row('?brow', mid, '?bg', vs)} }}\n"
            f"WHERE {{ {reif}\n"
            f"  BIND(IRI(CONCAT(\"urn:g:s:\", SHA256({_key(mid, vs)}))) AS ?bg)\n"
            f"  BIND(IRI(CONCAT(\"urn:row:\", SHA256({_key(mid, vs)}))) AS ?brow) }}")


def _join(midM, VM, midB, VB, midOut):
    newV = VM + [v for v in VB if v not in VM]
    q = (f"INSERT {{ ?prod a <{CC}Times> ; <{CC}in> ?gM ; <{CC}in> ?gB . {_row('?nrow', midOut, '?prod', newV)} }}\n"
         f"WHERE {{ {_row('?rM', midM, '?gM', VM)} {_row('?rB', midB, '?gB', VB)}\n"
         f"  BIND(IF(STR(?gM) <= STR(?gB), CONCAT(STR(?gM),\"|\",STR(?gB)), CONCAT(STR(?gB),\"|\",STR(?gM))) AS ?pk)\n"
         f"  BIND(IRI(CONCAT(\"urn:g:t:\", SHA256(?pk))) AS ?prod)\n"
         f"  BIND(IRI(CONCAT(\"urn:row:\", SHA256({_key(midOut, newV)}))) AS ?nrow) }}")
    return q, newV


def _marg(midIn, S, keep, midOut):
    return (f"INSERT {{ ?sum a <{CC}Plus> . ?src <{CC}feeds> ?sum . {_row('?mrow', midOut, '?sum', keep)} }}\n"
            f"WHERE {{ {_row('?r', midIn, '?src', S)}\n"
            f"  BIND(IRI(CONCAT(\"urn:g:s:\", SHA256({_key(midOut, keep)}))) AS ?sum)\n"
            f"  BIND(IRI(CONCAT(\"urn:row:\", SHA256({_key(midOut, keep)}))) AS ?mrow) }}")


def _answer(midF, O):
    return (f"INSERT {{ ?g <{CC}answer> ?key }}\n"
            f"WHERE {{ {_row('?r', midF, '?g', O)} BIND({_key('A', O)} AS ?key) }}")


def build(graph, patterns, out_vars):
    """Run the multi-pass plan on `graph` (already holding reified data). Returns
    (#passes, final message id). The circuit is left in `graph` as RDF."""
    O = set(out_vars)
    m = len(patterns)
    Vs = [_vars(p) for p in patterns]
    def frontier(i):
        f = set(O)
        for j in range(i + 1, m):
            f |= set(Vs[j])
        return f

    passes = 0
    for i, p in enumerate(patterns):
        graph.update(_base(p, Vs[i], f"B{i}")); passes += 1

    keep0 = [v for v in Vs[0] if v in frontier(0)]
    if keep0 != Vs[0]:
        graph.update(_marg("B0", Vs[0], keep0, "M0")); passes += 1
        curMID, curV = "M0", keep0
    else:
        curMID, curV = "B0", Vs[0]

    for i in range(1, m):
        jq, newV = _join(curMID, curV, f"B{i}", Vs[i], f"J{i}")
        graph.update(jq); passes += 1
        keep = [v for v in newV if v in frontier(i)]
        if keep != newV:
            graph.update(_marg(f"J{i}", newV, keep, f"M{i}")); passes += 1
            curMID, curV = f"M{i}", keep
        else:
            curMID, curV = f"J{i}", newV

    graph.update(_answer(curMID, out_vars)); passes += 1
    return passes, curMID
