#!/usr/bin/env python3
"""Regression for the native circuit interchange encoding."""

import circuit_io


def main():
    unicode_name = "\u53d8\u91cf"
    unicode_var = unicode_name.encode("utf-8").hex()
    nt = f"""\
<urn:g:t:{'2' * 32}> <urn:circuit:in> <urn:data:t0> .
<urn:g:t:{'2' * 32}> <urn:circuit:in> <urn:data:t1> .
<urn:g:t:{'2' * 32}> <urn:circuit:feeds> <urn:g:a:{'3' * 32}> .
<urn:g:a:{'3' * 32}> <urn:circuit:answerRoot> "vars:78,{unicode_var}" .
<urn:g:a:{'3' * 32}> <urn:circuit:bind:78> <urn:data:answer> .
<urn:g:sub:{'4' * 32}> <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <urn:circuit:Plus> .
"""
    circ, answers, bindings = circuit_io.parse(nt)
    answer = "urn:g:a:" + "3" * 32
    product = "urn:g:t:" + "2" * 32
    empty = "urn:g:sub:" + "4" * 32
    expected_binding = {
        "x": circuit_io.canon_iri("urn:data:answer"),
        unicode_name: "u",
    }
    if answers != {answer}:
        raise AssertionError(f"answer-root recovery failed: {answers!r}")
    if bindings != {answer: expected_binding}:
        raise AssertionError(f"term-aware binding recovery failed: {bindings!r}")
    if circ.get(product) != ("times", ("urn:data:t0", "urn:data:t1")):
        raise AssertionError(f"structural Times inference failed: {circ.get(product)!r}")
    if circ.get(answer) != ("plus", (product,)):
        raise AssertionError(f"structural Plus inference failed: {circ.get(answer)!r}")
    if circ.get(empty) != ("plus", ()):
        raise AssertionError(f"explicit empty Plus anchor was lost: {circ.get(empty)!r}")

    malformed = nt.replace("<urn:circuit:bind:78>", "<urn:circuit:bind:7>")
    try:
        circuit_io.parse(malformed)
    except circuit_io.CircuitFormatError:
        pass
    else:
        raise AssertionError("malformed variable encoding was accepted")

    conflict = nt + (
        f"<urn:g:t:{'2' * 32}> "
        "<http://www.w3.org/1999/02/22-rdf-syntax-ns#type> "
        "<urn:circuit:Plus> .\n"
    )
    try:
        circuit_io.parse(conflict)
    except circuit_io.CircuitFormatError:
        pass
    else:
        raise AssertionError("conflicting explicit and inferred gate types were accepted")
    print("CIRCUIT ENCODING I/O: ALL OK")


if __name__ == "__main__":
    main()
