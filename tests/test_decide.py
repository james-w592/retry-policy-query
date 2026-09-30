import unittest

from retryq.errors import PolicyError
from retryq.policy import decide, parse_string


class DecorrelatedJitterTests(unittest.TestCase):
    def test_parses_decorrelated_mode(self):
        policy = parse_string("jitter = decorrelated\n")
        self.assertEqual(policy.jitter, "decorrelated")

    def test_unknown_mode_still_rejected(self):
        with self.assertRaises(PolicyError) as ctx:
            parse_string("jitter = equal\n")
        self.assertEqual(ctx.exception.column, 10)

    def test_range_triples_each_attempt(self):
        policy = parse_string("max_attempts = 6\nbase_delay = 0.5\njitter = decorrelated\n")
        first = decide(policy, 1)
        self.assertTrue(first.should_retry)
        self.assertEqual(first.min_delay_seconds, 0.5)
        self.assertEqual(first.delay_seconds, 1.5)
        self.assertEqual(decide(policy, 2).delay_seconds, 4.5)
        self.assertEqual(decide(policy, 3).delay_seconds, 13.5)

    def test_multiplier_is_ignored(self):
        policy = parse_string("base_delay = 1\nmultiplier = 10\njitter = decorrelated\n")
        self.assertEqual(decide(policy, 1).delay_seconds, 3.0)

    def test_max_delay_caps_both_bounds(self):
        policy = parse_string("max_attempts = 9\nbase_delay = 4\nmax_delay = 10\njitter = decorrelated\n")
        decision = decide(policy, 5)
        self.assertEqual(decision.delay_seconds, 10.0)
        self.assertEqual(decision.min_delay_seconds, 4.0)

        tight = parse_string("base_delay = 4\nmax_delay = 2\njitter = decorrelated\n")
        decision = decide(tight, 1)
        self.assertEqual(decision.min_delay_seconds, 2.0)
        self.assertEqual(decision.delay_seconds, 2.0)

    def test_other_modes_have_no_lower_bound(self):
        policy = parse_string("jitter = full\n")
        decision = decide(policy, 1)
        self.assertIsNone(decision.min_delay_seconds)
        self.assertEqual(decision.jitter_mode, "full")
        self.assertTrue(decision.jittered)

    def test_no_retry_has_no_delay(self):
        policy = parse_string("max_attempts = 2\njitter = decorrelated\n")
        decision = decide(policy, 2)
        self.assertFalse(decision.should_retry)
        self.assertIsNone(decision.delay_seconds)
        self.assertIsNone(decision.min_delay_seconds)


if __name__ == "__main__":
    unittest.main()
