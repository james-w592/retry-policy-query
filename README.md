# retryq

A query tool for retry policies. You write down when something should
retry — how many times, how long to wait, which errors count — as a
small text file, and `retryq` answers one question: given a specific
attempt and outcome, does it retry, and after how long?

The idea is to make retry logic checkable without running the code
that has it. Retry rules tend to live scattered across decorators,
config dicts, and if-statements, and it's hard to answer "will attempt
4 against a 503 actually retry, or did that `except ValueError` above
it quietly eat the case?" without reading everything. Point `retryq`
at a plain description of the policy instead.

## Policy file format

One statement per line. Blank lines and lines starting with `#` are
ignored.

```
max_attempts = 5
base_delay = 0.5
multiplier = 2.0
max_delay = 30
jitter = full

retry_on status 500..599
retry_on exception TimeoutError, ConnectionError
give_up_on status 400..499
give_up_on exception ValueError
```

- `max_attempts`, `base_delay`, `multiplier`, `max_delay` — the usual
  exponential backoff parameters. Delay for attempt N is
  `base_delay * multiplier ** (N - 1)`, capped at `max_delay`.
- `jitter` — `none` or `full`. `full` means the real delay is a
  random value between 0 and the computed delay.
- `retry_on` / `give_up_on` — `status` takes HTTP codes and `low..high`
  ranges, `exception` takes exception class names. `give_up_on` always
  wins over `retry_on` when both match. If no `retry_on` rules are
  present at all, everything not explicitly given up on is retried.
- a rule can end with `except <values>` to carve exceptions out of an
  otherwise broad match, e.g. `retry_on status 500..599 except 501` or
  `give_up_on exception OSError except TimeoutError`. The `except`
  values are checked against the same kind (`status` or `exception`)
  as the rule they're attached to.

## Usage

```
$ retryq policy.retry --attempt 1 --status 503
retry: yes, wait 0.50s — matched retry_on rule

$ retryq policy.retry --attempt 3 --status 503
retry: yes, wait 2.00s — matched retry_on rule

$ retryq policy.retry --attempt 1 --status 404
retry: no — matched a give_up_on rule

$ retryq policy.retry --attempt 5 --status 503
retry: no — max_attempts (5) reached
```

(`jitter = none` in the examples above so the delays come out exact;
with `jitter = full` the output reads "wait up to 2.00s (full
jitter)".)

You can also use it as a library:

```python
from retryq import parse_file, decide

policy = parse_file("policy.retry")
decision = decide(policy, attempt=2, status=503)
print(decision.should_retry, decision.delay_seconds)
```

## Error messages

Policy files are meant to be read and reviewed like config, so parse
errors point at the exact character that's wrong instead of just
naming the line:

```
$ retryq policy.retry --attempt 1
policy.retry:2:14: error: expected a number, found 'fast'
    base_delay = fast
                 ^
```

## Requirements

Python 3.10+, standard library only. Nothing to install beyond the
package itself.

```
pip install -e .
```

## Status

Early skeleton. The policy format, the CLI flags, and the error
message wording are all still open to change.
