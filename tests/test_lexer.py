"""Tests for the line tokenizer in retryq.policy.

These poke at _tokenize_line directly rather than going through the
parser, so a change to token boundaries or column math shows up here
instead of being masked by whatever the parser happens to do with it.
"""

from __future__ import annotations

import unittest

from retryq.policy import _tokenize_line


def _kinds(line: str) -> list[tuple[str, str, int]]:
    return [(tok.kind, tok.text, tok.column) for tok in _tokenize_line(line)]


class TokenizeLineTests(unittest.TestCase):
    def test_assignment(self) -> None:
        self.assertEqual(
            _kinds("max_attempts = 5"),
            [("IDENT", "max_attempts", 1), ("EQUALS", "=", 14), ("NUMBER", "5", 16)],
        )

    def test_whitespace_is_dropped_not_kept(self) -> None:
        # tabs and runs of spaces both collapse to nothing between tokens
        self.assertEqual(
            _kinds("a\t=\t1"),
            [("IDENT", "a", 1), ("EQUALS", "=", 3), ("NUMBER", "1", 5)],
        )

    def test_comment_truncates_the_rest_of_the_line(self) -> None:
        self.assertEqual(
            _kinds("retry_on status 500 # keep going"),
            [("IDENT", "retry_on", 1), ("IDENT", "status", 10), ("NUMBER", "500", 17)],
        )

    def test_comment_only_line_yields_no_tokens(self) -> None:
        self.assertEqual(_kinds("   # nothing here"), [])

    def test_dotdot_does_not_swallow_a_leading_digit(self) -> None:
        # NUMBER is tried before DOTDOT, so "100..599" must split as
        # 100 / .. / 599, not gobble the first dot into the number.
        self.assertEqual(
            _kinds("100..599"),
            [("NUMBER", "100", 1), ("DOTDOT", "..", 4), ("NUMBER", "599", 6)],
        )

    def test_decimal_number_is_a_single_token(self) -> None:
        self.assertEqual(_kinds("0.5"), [("NUMBER", "0.5", 1)])

    def test_unrecognized_character_becomes_other(self) -> None:
        self.assertEqual(
            _kinds("$foo"),
            [("OTHER", "$", 1), ("IDENT", "foo", 2)],
        )

    def test_minus_sign_is_not_part_of_a_number(self) -> None:
        # there is no unary-minus token in this grammar: a "negative
        # number" is really OTHER("-") followed by a NUMBER.
        self.assertEqual(
            _kinds("-1"),
            [("OTHER", "-", 1), ("NUMBER", "1", 2)],
        )

    def test_comma_and_identifier_list(self) -> None:
        self.assertEqual(
            _kinds("TimeoutError, ConnectionError"),
            [
                ("IDENT", "TimeoutError", 1),
                ("COMMA", ",", 13),
                ("IDENT", "ConnectionError", 15),
            ],
        )


if __name__ == "__main__":
    unittest.main()
