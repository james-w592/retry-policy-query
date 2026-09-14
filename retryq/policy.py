"""Parsing and evaluation for retry policy files.

A policy file is a small line-oriented format, one statement per line:

    max_attempts = 5
    base_delay = 0.5
    multiplier = 2.0
    max_delay = 30
    jitter = full

    retry_on status 500..599
    retry_on exception TimeoutError, ConnectionError
    give_up_on status 400..499
    give_up_on exception ValueError

Blank lines and lines starting with '#' are ignored. Status ranges use
'..' rather than '-' so a range like 500..599 can't be confused with a
negative number.

A rule can carry an 'except' clause to carve out cases that would
otherwise match, the same way a broad 'except Exception' in code can
swallow more than intended:

    retry_on status 500..599 except 501
    retry_on exception OSError except FileNotFoundError, PermissionError
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .errors import PolicyError

_TOKEN_RE = re.compile(
    r"""
    (?P<NUMBER>\d+(?:\.\d+)?)
  | (?P<DOTDOT>\.\.)
  | (?P<EQUALS>=)
  | (?P<COMMA>,)
  | (?P<IDENT>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<WS>[ \t]+)
  | (?P<HASH>\#.*$)
  | (?P<OTHER>.)
    """,
    re.VERBOSE,
)

_ASSIGN_KEYS = {"max_attempts", "base_delay", "multiplier", "max_delay", "jitter"}
_JITTER_MODES = {"none", "full"}
_DIRECTIVES = _ASSIGN_KEYS | {"retry_on", "give_up_on"}


@dataclass(frozen=True)
class _Token:
    kind: str
    text: str
    column: int  # 1-based


def _tokenize_line(line: str) -> list[_Token]:
    tokens: list[_Token] = []
    for match in _TOKEN_RE.finditer(line):
        kind = match.lastgroup
        if kind == "WS":
            continue
        if kind == "HASH":
            break
        tokens.append(_Token(kind, match.group(), match.start() + 1))
    return tokens


@dataclass
class RetryPolicy:
    max_attempts: int = 3
    base_delay: float = 1.0
    multiplier: float = 2.0
    max_delay: float | None = None
    jitter: str = "none"
    retry_statuses: set[int] = field(default_factory=set)
    retry_status_ranges: list[tuple[int, int]] = field(default_factory=list)
    retry_exceptions: set[str] = field(default_factory=set)
    retry_status_exclusions: set[int] = field(default_factory=set)
    retry_status_exclusion_ranges: list[tuple[int, int]] = field(default_factory=list)
    retry_exception_exclusions: set[str] = field(default_factory=set)
    giveup_statuses: set[int] = field(default_factory=set)
    giveup_status_ranges: list[tuple[int, int]] = field(default_factory=list)
    giveup_exceptions: set[str] = field(default_factory=set)
    giveup_status_exclusions: set[int] = field(default_factory=set)
    giveup_status_exclusion_ranges: list[tuple[int, int]] = field(default_factory=list)
    giveup_exception_exclusions: set[str] = field(default_factory=set)

    @property
    def has_retry_rules(self) -> bool:
        return bool(self.retry_statuses or self.retry_status_ranges or self.retry_exceptions)

    @staticmethod
    def _status_in(status: int, singles: set[int], ranges: list[tuple[int, int]]) -> bool:
        if status in singles:
            return True
        return any(lo <= status <= hi for lo, hi in ranges)

    def matches_retry(self, *, status: int | None, exception: str | None) -> bool:
        if status is not None and self._status_in(status, self.retry_statuses, self.retry_status_ranges):
            if not self._status_in(status, self.retry_status_exclusions, self.retry_status_exclusion_ranges):
                return True
        if exception is not None and exception in self.retry_exceptions:
            if exception not in self.retry_exception_exclusions:
                return True
        return False

    def matches_giveup(self, *, status: int | None, exception: str | None) -> bool:
        if status is not None and self._status_in(status, self.giveup_statuses, self.giveup_status_ranges):
            if not self._status_in(status, self.giveup_status_exclusions, self.giveup_status_exclusion_ranges):
                return True
        if exception is not None and exception in self.giveup_exceptions:
            if exception not in self.giveup_exception_exclusions:
                return True
        return False


@dataclass(frozen=True)
class Decision:
    should_retry: bool
    attempt: int
    delay_seconds: float | None
    jittered: bool
    reason: str


def decide(
    policy: RetryPolicy,
    attempt: int,
    *,
    status: int | None = None,
    exception: str | None = None,
) -> Decision:
    if attempt < 1:
        raise ValueError("attempt must be >= 1")

    if policy.matches_giveup(status=status, exception=exception):
        return Decision(False, attempt, None, False, "matched a give_up_on rule")

    if attempt >= policy.max_attempts:
        return Decision(False, attempt, None, False, f"max_attempts ({policy.max_attempts}) reached")

    if policy.has_retry_rules and not policy.matches_retry(status=status, exception=exception):
        return Decision(False, attempt, None, False, "did not match any retry_on rule")

    delay = policy.base_delay * (policy.multiplier ** (attempt - 1))
    if policy.max_delay is not None:
        delay = min(delay, policy.max_delay)

    reason = "matched retry_on rule" if policy.has_retry_rules else "no retry_on rules; retrying by default"
    return Decision(True, attempt, delay, policy.jitter == "full", reason)


class _Parser:
    def __init__(self, filename: str, lines: list[str]) -> None:
        self.filename = filename
        self.lines = lines
        self.policy = RetryPolicy()
        self._set_keys: set[str] = set()

    def parse(self) -> RetryPolicy:
        for lineno, raw_line in enumerate(self.lines, start=1):
            stripped = raw_line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            tokens = _tokenize_line(raw_line)
            if not tokens:
                continue
            self._parse_statement(lineno, tokens)
        return self.policy

    def _error(self, lineno: int, column: int, message: str) -> PolicyError:
        source_line = self.lines[lineno - 1] if lineno - 1 < len(self.lines) else ""
        return PolicyError(message, filename=self.filename, line=lineno, column=column, source_line=source_line)

    def _parse_statement(self, lineno: int, tokens: list[_Token]) -> None:
        head = tokens[0]
        if head.kind != "IDENT":
            raise self._error(lineno, head.column, f"expected a directive name, found {head.text!r}")

        if head.text in _ASSIGN_KEYS:
            self._parse_assignment(lineno, tokens)
        elif head.text in ("retry_on", "give_up_on"):
            self._parse_rule(lineno, tokens, is_retry=(head.text == "retry_on"))
        else:
            raise self._error(
                lineno,
                head.column,
                f"unknown directive {head.text!r} (expected one of: {', '.join(sorted(_DIRECTIVES))})",
            )

    def _parse_assignment(self, lineno: int, tokens: list[_Token]) -> None:
        key_tok = tokens[0]
        if len(tokens) < 2 or tokens[1].kind != "EQUALS":
            col = tokens[1].column if len(tokens) > 1 else key_tok.column + len(key_tok.text)
            raise self._error(lineno, col, f"expected '=' after {key_tok.text!r}")
        if len(tokens) < 3:
            raise self._error(lineno, tokens[1].column + 2, f"expected a value after '{key_tok.text} ='")
        if len(tokens) > 3:
            extra = tokens[3]
            raise self._error(lineno, extra.column, f"unexpected extra text after value: {extra.text!r}")

        value_tok = tokens[2]
        key = key_tok.text

        if key in self._set_keys:
            raise self._error(lineno, key_tok.column, f"{key!r} is set more than once")
        self._set_keys.add(key)

        if key == "jitter":
            if value_tok.kind != "IDENT" or value_tok.text not in _JITTER_MODES:
                raise self._error(
                    lineno,
                    value_tok.column,
                    f"jitter must be one of {sorted(_JITTER_MODES)}, found {value_tok.text!r}",
                )
            self.policy.jitter = value_tok.text
            return

        if value_tok.kind != "NUMBER":
            raise self._error(lineno, value_tok.column, f"expected a number, found {value_tok.text!r}")

        number: float = float(value_tok.text) if "." in value_tok.text else int(value_tok.text)

        if key == "max_attempts":
            if isinstance(number, float) or number < 1:
                raise self._error(lineno, value_tok.column, "max_attempts must be a positive whole number")
            self.policy.max_attempts = int(number)
        elif key == "base_delay":
            if number < 0:
                raise self._error(lineno, value_tok.column, "base_delay cannot be negative")
            self.policy.base_delay = float(number)
        elif key == "multiplier":
            if number < 0:
                raise self._error(lineno, value_tok.column, "multiplier cannot be negative")
            self.policy.multiplier = float(number)
        elif key == "max_delay":
            if number < 0:
                raise self._error(lineno, value_tok.column, "max_delay cannot be negative")
            self.policy.max_delay = float(number)

    def _parse_rule(self, lineno: int, tokens: list[_Token], *, is_retry: bool) -> None:
        directive = tokens[0].text
        if len(tokens) < 2 or tokens[1].kind != "IDENT" or tokens[1].text not in ("status", "exception"):
            col = tokens[1].column if len(tokens) > 1 else tokens[0].column + len(directive) + 1
            raise self._error(lineno, col, f"expected 'status' or 'exception' after {directive!r}")

        kind = tokens[1].text
        rest = tokens[2:]
        if not rest:
            raise self._error(
                lineno, tokens[1].column + len(kind) + 1, f"expected at least one value after {directive} {kind}"
            )

        except_index = self._find_except(rest)
        main_tokens = rest[:except_index] if except_index is not None else rest
        if not main_tokens:
            raise self._error(lineno, rest[0].column, "expected at least one value before 'except'")

        parse_list = self._parse_status_list if kind == "status" else self._parse_exception_list
        parse_list(lineno, main_tokens, is_retry=is_retry, exclude=False)

        if except_index is not None:
            except_tok = rest[except_index]
            exclusion_tokens = rest[except_index + 1 :]
            second_except = self._find_except(exclusion_tokens)
            if second_except is not None:
                raise self._error(lineno, exclusion_tokens[second_except].column, "'except' can only appear once per rule")
            if not exclusion_tokens:
                raise self._error(
                    lineno, except_tok.column + len(except_tok.text), "expected at least one value after 'except'"
                )
            parse_list(lineno, exclusion_tokens, is_retry=is_retry, exclude=True)

    @staticmethod
    def _find_except(tokens: list[_Token]) -> int | None:
        for i, tok in enumerate(tokens):
            if tok.kind == "IDENT" and tok.text == "except":
                return i
        return None

    def _parse_status_list(self, lineno: int, tokens: list[_Token], *, is_retry: bool, exclude: bool = False) -> None:
        i = 0
        expect_value = True
        while i < len(tokens):
            tok = tokens[i]
            if expect_value:
                if tok.kind != "NUMBER" or "." in tok.text:
                    raise self._error(lineno, tok.column, f"expected a status code, found {tok.text!r}")
                low = int(tok.text)
                if not (100 <= low <= 599):
                    raise self._error(lineno, tok.column, f"{low} is not a valid HTTP status code (100-599)")
                i += 1
                if i < len(tokens) and tokens[i].kind == "DOTDOT":
                    dotdot_tok = tokens[i]
                    i += 1
                    if i >= len(tokens) or tokens[i].kind != "NUMBER" or "." in tokens[i].text:
                        col = tokens[i].column if i < len(tokens) else dotdot_tok.column + 2
                        raise self._error(lineno, col, "expected a status code after '..'")
                    high_tok = tokens[i]
                    high = int(high_tok.text)
                    if not (100 <= high <= 599):
                        raise self._error(lineno, high_tok.column, f"{high} is not a valid HTTP status code (100-599)")
                    if high < low:
                        raise self._error(lineno, high_tok.column, f"range {low}..{high} goes backwards")
                    self._add_status_range(low, high, is_retry=is_retry, exclude=exclude)
                    i += 1
                else:
                    self._add_status(low, is_retry=is_retry, exclude=exclude)
                expect_value = False
            else:
                if tok.kind != "COMMA":
                    raise self._error(lineno, tok.column, f"expected ',' between status values, found {tok.text!r}")
                i += 1
                expect_value = True
        if expect_value:
            last = tokens[-1]
            raise self._error(lineno, last.column + len(last.text), "trailing ',' with nothing after it")

    def _add_status(self, code: int, *, is_retry: bool, exclude: bool = False) -> None:
        if exclude:
            target = self.policy.retry_status_exclusions if is_retry else self.policy.giveup_status_exclusions
        else:
            target = self.policy.retry_statuses if is_retry else self.policy.giveup_statuses
        target.add(code)

    def _add_status_range(self, low: int, high: int, *, is_retry: bool, exclude: bool = False) -> None:
        if exclude:
            target = self.policy.retry_status_exclusion_ranges if is_retry else self.policy.giveup_status_exclusion_ranges
        else:
            target = self.policy.retry_status_ranges if is_retry else self.policy.giveup_status_ranges
        target.append((low, high))

    def _parse_exception_list(self, lineno: int, tokens: list[_Token], *, is_retry: bool, exclude: bool = False) -> None:
        i = 0
        expect_value = True
        while i < len(tokens):
            tok = tokens[i]
            if expect_value:
                if tok.kind != "IDENT":
                    raise self._error(lineno, tok.column, f"expected an exception name, found {tok.text!r}")
                if exclude:
                    target = self.policy.retry_exception_exclusions if is_retry else self.policy.giveup_exception_exclusions
                else:
                    target = self.policy.retry_exceptions if is_retry else self.policy.giveup_exceptions
                target.add(tok.text)
                i += 1
                expect_value = False
            else:
                if tok.kind != "COMMA":
                    raise self._error(lineno, tok.column, f"expected ',' between exception names, found {tok.text!r}")
                i += 1
                expect_value = True
        if expect_value:
            last = tokens[-1]
            raise self._error(lineno, last.column + len(last.text), "trailing ',' with nothing after it")


def parse_string(text: str, *, filename: str = "<policy>") -> RetryPolicy:
    return _Parser(filename, text.splitlines()).parse()


def parse_file(path: str) -> RetryPolicy:
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    return parse_string(text, filename=path)
