"""Command-line interface: answer 'will this attempt retry, and when?'"""

from __future__ import annotations

import argparse
import sys

from .errors import PolicyError
from .policy import RetryPolicy, decide, lint_policy, parse_file


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="retryq",
        description="Query a retry policy file: will a given attempt retry, and after how long?",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser("check", help="evaluate one attempt against a policy")
    check.add_argument("policy_file", help="path to a .retry policy file")
    check.add_argument("--attempt", type=int, required=True, help="attempt number to evaluate (1 = first try)")
    check.add_argument("--status", type=int, default=None, help="HTTP status code returned by the attempt")
    check.add_argument("--exception", default=None, help="exception class name raised by the attempt")

    lint = subparsers.add_parser("lint", help="validate a policy file without evaluating it")
    lint.add_argument("policy_file", help="path to a .retry policy file")

    return parser


def _load_policy(policy_file: str) -> RetryPolicy | None:
    try:
        return parse_file(policy_file)
    except PolicyError as exc:
        print(exc, file=sys.stderr)
        return None
    except OSError as exc:
        print(f"retryq: cannot read {policy_file!r}: {exc.strerror}", file=sys.stderr)
        return None


def _run_check(args: argparse.Namespace) -> int:
    policy = _load_policy(args.policy_file)
    if policy is None:
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


def _run_lint(args: argparse.Namespace) -> int:
    policy = _load_policy(args.policy_file)
    if policy is None:
        return 1

    warnings = lint_policy(policy)
    for warning in warnings:
        print(f"{args.policy_file}: warning: {warning}")

    if warnings:
        print(f"{args.policy_file}: {len(warnings)} warning(s)")
    else:
        print(f"{args.policy_file}: OK")

    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.command == "lint":
        return _run_lint(args)
    return _run_check(args)


if __name__ == "__main__":
    sys.exit(main())
