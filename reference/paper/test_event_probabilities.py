"""Regression tests for fixed-seed per-event probabilities."""

from pathlib import Path
import sys
import unittest


REFERENCE = Path(__file__).resolve().parents[1]
if str(REFERENCE) not in sys.path:
    sys.path.insert(0, str(REFERENCE))

import event_probabilities


class EventProbabilitiesTest(unittest.TestCase):
    def test_mapping_is_stable_per_event_and_varies_between_events(self):
        seed = event_probabilities.DEFAULT_PROBABILITY_SEED
        first = event_probabilities.event_probability("urn:event:1", seed)
        repeated = event_probabilities.event_probability("urn:event:1", seed)
        second = event_probabilities.event_probability("urn:event:2", seed)

        self.assertEqual(first, repeated)
        self.assertNotEqual(first, second)
        self.assertGreater(first, 0.0)
        self.assertLess(first, 1.0)

    def test_mapping_does_not_depend_on_input_order(self):
        seed = event_probabilities.DEFAULT_PROBABILITY_SEED
        forward = event_probabilities.event_weights(("urn:event:1", "urn:event:2"), seed)
        reverse = event_probabilities.event_weights(("urn:event:2", "urn:event:1"), seed)

        self.assertEqual(forward, reverse)


if __name__ == "__main__":
    unittest.main()
