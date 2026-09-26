"""Tests for retryq.policy.parse_string / parse_file.

Beyond checking that valid policies parse into the fields we expect,
most of this file pins down exact error line/column/message for
malformed input. The error position is the entire point of PolicyError,
so a regression there (an off-by-one column, a message that stops
matching the token that actually caused it) is worth catching in a
test, not just noticed by eye later.
"""

from __future__ import annotations

import os
import tempfile
import unittest

from retryq.errors import PolicyError
from retryq.policy import parse_file, parse_string


def _error(text: str) -> PolicyError:
    with unittest.TestCase().assertRaises(PolicyError) as ctx:
        parse_string(text)
    return ctx.exception


class ValidPoliciesTests(unittest.TestCase):
    def test_full_example_from_module_docstring(self) -> None:
        policy = parse_string(
            "max_attempts = 5\n"
            "base_delay = 0.5\n"
            "multiplier = 2.0\n"
            "max_delay = 30\n"
            "jitter = full\n"
            "\n"
            "retry_on status 500..599\n"
            "retry_on exception TimeoutError, ConnectionError\n"
            "give_up_on status 400..499\n"
            "give_up_on exception ValueError\n"
        )
        self.assertEqual(policy.max_attempts, 5)
        self.assertEqual(policy.base_delay, 0.5)
        self.assertEqual(policy.multiplier, 2.0)
        self.assertEqual(policy.max_delay, 30.0)
        self.assertEqual(policy.jitter, "full")
        self.assertEqual(policy.retry_status_ranges, [(500, 599)])
        self.assertEqual(policy.retry_exceptions, {"TimeoutError", "ConnectionError"})
        self.assertEqual(policy.giveup_status_ranges, [(400, 499)])
        self.assertEqual(policy.giveup_exceptions, {"ValueError"})

    def test_blank_lines_and_comments_are_ignored(self) -> None:
        policy = parse_string("\n# a comment\n\nmax_attempts = 2\n   # trailing\n")
        self.assertEqual(policy.max_attempts, 2)

    def test_defaults_when_nothing_set(self) -> None:
        policy = parse_string("")
        self.assertEqual(policy.max_attempts, 3)
        self.assertEqual(policy.base_delay, 1.0)
        self.assertEqual(policy.multiplier, 2.0)
        self.assertIsNone(policy.max_delay)
        self.assertEqual(policy.jitter, "none")
        self.assertFalse(policy.has_retry_rules)

    def test_except_clauses_populate_exclusion_sets(self) -> None:
        policy = parse_string(
            "retry_on status 500..599 except 501\n"
            "retry_on exception OSError except FileNotFoundError, PermissionError\n"
        )
        self.assertEqual(policy.retry_status_ranges, [(500, 599)])
        self.assertEqual(policy.retry_status_exclusions, {501})
        self.assertEqual(policy.retry_exceptions, {"OSError"})
        self.assertEqual(
            policy.retry_exception_exclusions, {"FileNotFoundError", "PermissionError"}
        )

    def test_give_up_on_targets_giveup_fields_not_retry_fields(self) -> None:
        policy = parse_string("give_up_on status 404\n")
        self.assertEqual(policy.giveup_statuses, {404})
        self.assertEqual(policy.retry_statuses, set())

    def test_integer_valued_float_fields_are_stored_as_float(self) -> None:
        policy = parse_string("max_delay = 30\n")
        self.assertIsInstance(policy.max_delay, float)
        self.assertEqual(policy.max_delay, 30.0)

    def test_parse_file_reads_from_disk(self) -> None:
        fd, path = tempfile.mkstemp(suffix=".retry")
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write("max_attempts = 7\n")
            policy = parse_file(path)
            self.assertEqual(policy.max_attempts, 7)
        finally:
            os.remove(path)


class DirectiveErrorTests(unittest.TestCase):
    def test_unrecognized_character_is_not_a_directive_name(self) -> None:
        exc = _error("$foo = 5\n")
        self.assertEqual(exc.line, 1)
        self.assertEqual(exc.column, 1)
        self.assertIn("expected a directive name, found '$'", exc.message)

    def test_unknown_directive_lists_valid_ones(self) -> None:
        exc = _error("frobnicate = 5\n")
        self.assertEqual(
            exc.message,
            "unknown directive 'frobnicate' (expected one of: base_delay, "
            "give_up_on, jitter, max_attempts, max_delay, multiplier, retry_on)",
        )


class AssignmentErrorTests(unittest.TestCase):
    def test_missing_equals_with_value_present(self) -> None:
        exc = _error("max_attempts 5\n")
        self.assertEqual(exc.column, 14)
        self.assertIn("expected '=' after 'max_attempts'", exc.message)

    def test_missing_equals_at_end_of_line(self) -> None:
        exc = _error("max_attempts\n")
        self.assertEqual(exc.column, 13)
        self.assertIn("expected '=' after 'max_attempts'", exc.message)

    def test_missing_value(self) -> None:
        exc = _error("max_attempts =\n")
        self.assertEqual(exc.column, 16)
        self.assertIn("expected a value after 'max_attempts ='", exc.message)

    def test_extra_text_after_value(self) -> None:
        exc = _error("max_attempts = 5 6\n")
        self.assertEqual(exc.column, 18)
        self.assertIn("unexpected extra text after value: '6'", exc.message)

    def test_negative_literal_has_no_minus_token_so_it_reads_as_extra_text(self) -> None:
        # documents real behavior: '-' tokenizes as OTHER, not a sign,
        # so "base_delay = -1" fails as "extra text" rather than as a
        # clean negative-number rejection.
        exc = _error("base_delay = -1\n")
        self.assertEqual(exc.column, 15)
        self.assertIn("unexpected extra text after value: '1'", exc.message)

    def test_duplicate_key(self) -> None:
        exc = _error("max_attempts = 5\nmax_attempts = 3\n")
        self.assertEqual(exc.line, 2)
        self.assertEqual(exc.column, 1)
        self.assertIn("'max_attempts' is set more than once", exc.message)

    def test_jitter_rejects_unknown_mode(self) -> None:
        exc = _error("jitter = fast\n")
        self.assertEqual(exc.column, 10)
        self.assertIn("jitter must be one of ['full', 'none'], found 'fast'", exc.message)

    def test_jitter_accepts_none_and_full(self) -> None:
        self.assertEqual(parse_string("jitter = none\n").jitter, "none")
        self.assertEqual(parse_string("jitter = full\n").jitter, "full")

    def test_max_attempts_rejects_fractional_value(self) -> None:
        exc = _error("max_attempts = 5.0\n")
        self.assertIn("max_attempts must be a positive whole number", exc.message)

    def test_max_attempts_rejects_zero(self) -> None:
        exc = _error("max_attempts = 0\n")
        self.assertIn("max_attempts must be a positive whole number", exc.message)

    def test_base_delay_rejects_non_number(self) -> None:
        exc = _error("base_delay = fast\n")
        self.assertIn("expected a number, found 'fast'", exc.message)


class RuleErrorTests(unittest.TestCase):
    def test_missing_kind_at_end_of_line(self) -> None:
        exc = _error("retry_on\n")
        self.assertEqual(exc.column, 10)
        self.assertIn("expected 'status' or 'exception' after 'retry_on'", exc.message)

    def test_unrecognized_kind(self) -> None:
        exc = _error("retry_on foo 500\n")
        self.assertEqual(exc.column, 10)
        self.assertIn("expected 'status' or 'exception' after 'retry_on'", exc.message)

    def test_kind_with_no_values(self) -> None:
        exc = _error("retry_on status\n")
        self.assertEqual(exc.column, 17)
        self.assertIn("expected at least one value after retry_on status", exc.message)

    def test_except_with_nothing_before_it(self) -> None:
        exc = _error("retry_on status except 501\n")
        self.assertEqual(exc.column, 17)
        self.assertIn("expected at least one value before 'except'", exc.message)

    def test_except_may_only_appear_once(self) -> None:
        exc = _error("retry_on status 500 except 501 except 502\n")
        self.assertEqual(exc.column, 32)
        self.assertIn("'except' can only appear once per rule", exc.message)

    def test_except_with_nothing_after_it(self) -> None:
        exc = _error("retry_on status 500 except\n")
        self.assertEqual(exc.column, 27)
        self.assertIn("expected at least one value after 'except'", exc.message)


class StatusListErrorTests(unittest.TestCase):
    def test_out_of_range_status_code(self) -> None:
        exc = _error("retry_on status 999\n")
        self.assertEqual(exc.column, 17)
        self.assertIn("999 is not a valid HTTP status code (100-599)", exc.message)

    def test_backwards_range(self) -> None:
        exc = _error("retry_on status 599..500\n")
        self.assertEqual(exc.column, 22)
        self.assertIn("range 599..500 goes backwards", exc.message)

    def test_range_missing_high_end(self) -> None:
        exc = _error("retry_on status 500..\n")
        self.assertEqual(exc.column, 22)
        self.assertIn("expected a status code after '..'", exc.message)

    def test_decimal_status_code_is_rejected(self) -> None:
        exc = _error("retry_on status 200.5\n")
        self.assertEqual(exc.column, 17)
        self.assertIn("expected a status code, found '200.5'", exc.message)

    def test_trailing_comma(self) -> None:
        exc = _error("retry_on status 500,\n")
        self.assertEqual(exc.column, 21)
        self.assertIn("trailing ',' with nothing after it", exc.message)

    def test_missing_comma_between_values(self) -> None:
        exc = _error("retry_on status 500 501\n")
        self.assertEqual(exc.column, 21)
        self.assertIn("expected ',' between status values, found '501'", exc.message)

    def test_valid_range_and_singleton_together(self) -> None:
        policy = parse_string("retry_on status 500..504, 599\n")
        self.assertEqual(policy.retry_status_ranges, [(500, 504)])
        self.assertEqual(policy.retry_statuses, {599})


class ExceptionListErrorTests(unittest.TestCase):
    def test_non_identifier_value_is_rejected(self) -> None:
        exc = _error("retry_on exception 500\n")
        self.assertEqual(exc.column, 20)
        self.assertIn("expected an exception name, found '500'", exc.message)

    def test_trailing_comma(self) -> None:
        exc = _error("retry_on exception TimeoutError,\n")
        self.assertEqual(exc.column, 33)
        self.assertIn("trailing ',' with nothing after it", exc.message)

    def test_missing_comma_between_values(self) -> None:
        exc = _error("retry_on exception TimeoutError ConnectionError\n")
        self.assertEqual(exc.column, 33)
        self.assertIn(
            "expected ',' between exception names, found 'ConnectionError'", exc.message
        )


class PolicyErrorFormattingTests(unittest.TestCase):
    def test_str_includes_filename_source_line_and_caret(self) -> None:
        try:
            parse_string("base_delay = fast\n", filename="policy.retry")
        except PolicyError as exc:
            text = str(exc)
        else:
            self.fail("expected PolicyError")

        self.assertIn("policy.retry:1:14: error: expected a number, found 'fast'", text)
        self.assertIn("base_delay = fast", text)
        self.assertIn(" " * 13 + "^", text)


if __name__ == "__main__":
    unittest.main()
