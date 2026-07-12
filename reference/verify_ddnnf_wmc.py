"""Offline regression for the one-compile d-DNNF evaluator."""
import sys
from ddnnf_wmc import evaluate_text, NNFError


P = {1: (0.2, 0.8), 2: (0.3, 0.7)}
EXPECTED = 0.2 + 0.8 * 0.3                  # x OR (~x AND y)

CLASSIC = """nnf 5 5 2
L 1
L -1
L 2
A 2 1 2
O 0 2 0 3
"""

D4 = """o 1 0
t 2 0
t 3 0
1 2 1 0
1 3 -1 2 0
"""

# Variable 3 is a Tseitin auxiliary: either sign is projected away.
AUX = """nnf 3 2 3
L 1
L 3
A 2 0 1
"""

# Official d4/c2d-format example: 4 satisfying assignments out of 2^3.
D4_CLASSIC_EXAMPLE = """nnf 15 4 3
O 0 0
A 0
L -1
L 3
A 3 3 2 1
L 1
A 2 5 1
O 1 2 6 4
L -2
A 2 8 7
L -1
L 2
L -3
A 4 12 11 10 1
O 2 2 13 9
"""


def main():
    c = evaluate_text(CLASSIC, P)
    d = evaluate_text(D4, P)
    a = evaluate_text(AUX, P)
    ex = evaluate_text(D4_CLASSIC_EXAMPLE, {1: (0.5, 0.5), 2: (0.5, 0.5), 3: (0.5, 0.5)})
    malformed_rejected = False
    try:
        evaluate_text("O 0 1 99\n", P)
    except NNFError:
        malformed_rejected = True
    ok = (abs(c.probability - EXPECTED) < 1e-12 and
          abs(d.probability - EXPECTED) < 1e-12 and
          abs(a.probability - 0.2) < 1e-12 and abs(ex.probability - 0.5) < 1e-12 and malformed_rejected)
    print(f"[classic] {c.probability:.6f} expected={EXPECTED:.6f}")
    print(f"[d4     ] {d.probability:.6f} expected={EXPECTED:.6f}")
    print(f"[aux-proj] {a.probability:.6f} expected=0.200000")
    print(f"[d4 example] {ex.probability:.6f} expected=0.500000")
    print(f"[malformed] rejected={malformed_rejected}")
    print("\nALL OK" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
