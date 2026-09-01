#!/usr/bin/env bash
# Guard rail: the CONSTRUCT plan emitted for every evaluation query must not change.
#
# Gate IRIs are content-addressed, so any change to the rewriting that moves a key moves
# the circuit -- and with it the node counts reported for compactness, the cross-engine
# byte-identity gallery, and every checked-in fixture. Those are published numbers. This
# script makes "did I change an already-measured circuit?" an explicit yes/no rather than
# something noticed later.
#
#   engine/verify/plan-identity.sh            compare against the baseline
#   engine/verify/plan-identity.sh --update   accept the current plans as the new baseline
#
# --update is a deliberate act: commit the baseline diff together with the change that
# caused it, and say in the message why the circuits were allowed to move.
#
# The factorised plan names its private message relations after a per-run UUID, so that one
# field is masked before comparing; everything else, gate keys included, must match.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
JAR="${JAR:-$ROOT/engine/target/npcs-rewrite.jar}"
BASELINE="$HERE/plan-identity.baseline"
EMPTY_DATA="$(mktemp)"; trap 'rm -f "$EMPTY_DATA"' EXIT
: > "$EMPTY_DATA"                      # planning needs no data; only the emitted text matters

[ -f "$JAR" ] || { echo "missing $JAR -- run: mvn -q -f engine/pom.xml package" >&2; exit 2; }

queries() {
  # These are explicit regression fixtures, not measured experiment inputs.
  # Keeping them beside the verifier prevents workload cleanup from breaking
  # the byte-identity guard.
  ls "$HERE"/queries/watdiv/*/*.rq
  find "$HERE/queries/skeletons" -name '*.rq' | sort
}

current() {
  for q in $(queries); do
    for mode in flat factorised; do
      echo "### $(basename "$(dirname "$q")")/$(basename "$q") [$mode]"
      java -cp "$JAR" npcs.circuit.CircuitRun --construction=$mode Standard \
           "$EMPTY_DATA" "$q" 2>&1 >/dev/null \
        | grep -v '^# construction_ms' \
        | sed -e 's/urn:sc:msg:[0-9a-f]\{64\}:/urn:sc:msg:WORKSPACE:/g' \
              -e 's/[[:blank:]]*$//'
    done
  done
}

if [ "${1:-}" = "--update" ]; then
  current > "$BASELINE"
  echo "baseline updated: $(grep -c '^### ' "$BASELINE") plans, $(wc -l < "$BASELINE") lines"
  echo "commit it with the change that caused it, and say why the circuits moved."
  exit 0
fi

[ -f "$BASELINE" ] || { echo "no baseline yet -- run: $0 --update" >&2; exit 2; }

if diff -u "$BASELINE" <(current) > /tmp/plan-identity.$$ 2>&1; then
  echo "PLAN IDENTITY OK: $(grep -c '^### ' "$BASELINE") plans byte-identical to the baseline"
  rm -f /tmp/plan-identity.$$
else
  echo "PLAN IDENTITY CHANGED -- an already-measured circuit would move:"
  head -40 /tmp/plan-identity.$$
  echo "..."
  echo "(full diff in /tmp/plan-identity.$$; if intended, re-run with --update)"
  exit 1
fi
