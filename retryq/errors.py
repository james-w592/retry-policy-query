"""Error type for retry policy files, with source-position context.

A parse error that just says "invalid value" is useless once a policy
file has more than five lines in it. Every error here carries the
exact line and column where things went wrong, plus the offending
source line, so the message can point at the problem instead of just
naming it.
"""

from __future__ import annotations


class PolicyError(Exception):
    def __init__(
        self,
        message: str,
        *,
        filename: str,
        line: int,
        column: int,
        source_line: str,
    ) -> None:
        self.message = message
        self.filename = filename
        self.line = line
        self.column = column
        self.source_line = source_line
        super().__init__(self.format())

    def format(self) -> str:
        pointer = " " * (self.column - 1) + "^"
        return (
            f"{self.filename}:{self.line}:{self.column}: error: {self.message}\n"
            f"    {self.source_line}\n"
            f"    {pointer}"
        )

    def __str__(self) -> str:
        return self.format()
