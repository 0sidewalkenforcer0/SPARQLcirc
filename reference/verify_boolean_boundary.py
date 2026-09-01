"""Regression for the documented N[X] -> Boolean RDF serialization boundary."""
import circuit_io
import compile_bdd
import gates


def main():
    # The algebraic Python model keeps occurrence multiplicity.
    algebraic = gates.Circuit()
    x = algebraic.leaf("urn:test:x")
    tx = algebraic.times([x, x])
    px = algebraic.plus([x, x])
    assert algebraic.gates[tx][1] == (x, x)
    assert algebraic.gates[px][1] == (x, x)

    # Ordinary RDF cannot store the same edge twice. circuit_io intentionally
    # consumes that representation as a Boolean event circuit.
    nt = """\
<urn:test:t> <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <urn:circuit:Times> .
<urn:test:t> <urn:circuit:in> <urn:test:x> .
<urn:test:t> <urn:circuit:in> <urn:test:x> .
<urn:test:t> <urn:circuit:feeds> <urn:test:a> .
<urn:test:t> <urn:circuit:feeds> <urn:test:a> .
<urn:test:a> <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <urn:circuit:Plus> .
<urn:test:a> <urn:circuit:answer> "A" .
"""
    boolean, answers, _ = circuit_io.parse(nt)
    assert boolean["urn:test:t"] == ("times", ("urn:test:x",))
    assert boolean["urn:test:a"] == ("plus", ("urn:test:t",))
    assert answers == {"urn:test:a"}
    p = {"urn:test:x": 0.37}
    assert abs(compile_bdd.probability(boolean, "urn:test:a", p)[0] - 0.37) < 1e-12
    print("N[X] multiplicity / Boolean RDF boundary: ALL OK")


if __name__ == "__main__":
    main()
