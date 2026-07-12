"""Offline regression for the forced per-answer d4v2 comparison harness."""
import os
import stat
import tempfile

import compile_portfolio
import d4_pipeline
import level1_d4_headtohead as l1


def check_one_compiler_invocation():
    with tempfile.TemporaryDirectory() as d:
        fake = os.path.join(d, "d4v2")
        count = os.path.join(d, "count")
        script = f'''#!/usr/bin/env python3
import sys
args = sys.argv[1:]
out = args[args.index("--dump-file") + 1]
open(out, "w").write("o 1 0\\nt 2 0\\n1 2 1 0\\n")
open({count!r}, "a").write("1\\n")
'''
        with open(fake, "w") as fh: fh.write(script)
        os.chmod(fake, os.stat(fake).st_mode | stat.S_IXUSR)
        old_v2 = d4_pipeline.V2
        d4_pipeline.V2 = "1"
        try:
            r = compile_portfolio.d4_compile_once({"r": ("leaf", "tok")}, "r", {"tok": 0.2}, d4bin=fake)
        finally:
            d4_pipeline.V2 = old_v2
        calls = len(open(count).read().splitlines())
    ok = abs(r["probability"] - 0.2) < 1e-12 and calls == 1 and r["nnf_format"] == "d4"
    print(f"[one compile] p={r['probability']:.6f} subprocesses={calls} {'OK' if ok else 'FAIL'}")
    return ok


def check_answer_ids_and_sql():
    q3 = ("A|line=i\x1fhttp://example.org/Lineitem/42-3|"
          "order=i\x1fhttp://example.org/Order/42")
    qr = "A|cust=i\x1fhttp://example.org/Customer/17"
    old_d4 = os.environ.get("D4")
    os.environ["D4"] = "/tmp/pinned/d4v2"
    try:
        sql = l1.provsql_sql(l1.SPECS[0], True)
    finally:
        if old_d4 is None: os.environ.pop("D4", None)
        else: os.environ["D4"] = old_d4
    ok = (l1.answer_id("q3", q3) == "42:3" and l1.answer_id("qrecon", qr) == "17" and
          "probability_evaluate(prov, 'compilation', 'd4v2-cnf')" in sql and
          "input_formats=ARRAY['dimacs-cnf']" in sql and "SELECT 'P'" in sql)
    print(f"[ids + SQL ] explicit CNF-only d4v2 + stable keys {'OK' if ok else 'FAIL'}")
    return ok


def check_parity():
    spec = l1.Spec("x", "", "", "", "qrecon", 2)
    ours = {"per": {"1": 0.375, "2": 0.4375}}
    prov = {"per": {"1": (0.375, 2), "2": (0.4375, 3)}}
    good = l1.parity(spec, ours, prov)
    bad = l1.parity(spec, ours, {"per": {"1": (0.375, 2)}})
    ok = good["agree"] and not bad["agree"]
    print(f"[key parity] complete accepted, missing key rejected {'OK' if ok else 'FAIL'}")
    return ok


def check_provsql_parser():
    summary, per = l1.parse_provsql_rows(
        "S|2|0.375|0.4375|0.8125\nP|1|0.375|2\nP|2|0.4375|3\n")
    ok = (summary == {"answers": 2, "p_min": 0.375, "p_max": 0.4375, "p_sum": 0.8125} and
          per == {"1": (0.375, 2), "2": (0.4375, 3)})
    print(f"[SQL parser] tagged summary + keyed probabilities {'OK' if ok else 'FAIL'}")
    return ok


if __name__ == "__main__":
    success = (check_one_compiler_invocation() and check_answer_ids_and_sql() and check_parity() and
               check_provsql_parser())
    print("\nALL OK" if success else "\nFAIL")
    raise SystemExit(0 if success else 1)
