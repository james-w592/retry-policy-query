"""Command-line interface: answer 'will this attempt retry, and when?'"""

from __future__ import annotations

import argparse
import sys

from .errors import PolicyError
from .policy import decide, parse_file


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="retryq",
        description="Query a retry policy file: will a given attempt retry, and after how long?",
    )
    parser.add_argument("policy_file", help="path to a .retry policy file")
    parser.add_argument("--attempt", type=int, required=True, help="attempt number to evaluate (1 = first try)")
    parser.add_argument("--status", type=int, default=None, help="HTTP status code returned by the attempt")
    parser.add_argument("--exception", default=None, help="exception class name raised by the attempt")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    try:
        policy = parse_file(args.policy_file)
    except PolicyError as exc:
        print(exc, file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"retryq: cannot read {args.policy_file!r}: {exc.strerror}", file=sys.stderr)
        return 1

    if args.attempt < 1:
        print("retryq: --attempt must be 1 or greater", file=sys.stderr)
        return 1

    decision = decide(policy, args.attempt, status=args.status, exception=args.exception)

    if decision.should_retry:
        assert decision.delay_seconds is not None
        if decision.jittered:
            print(f"retry: yes, wait up to {decision.delay_seconds:.2f}s (full jitter) — {decision.reason}")
        else:
            print(f"retry: yes, wait {decision.delay_seconds:.2f}s — {decision.reason}")
    else:
        print(f"retry: no — {decision.reason}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
