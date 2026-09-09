"""retryq: answer one question about a retry policy — will this attempt retry, and when?"""

from .errors import PolicyError
from .policy import Decision, RetryPolicy, decide, parse_file, parse_string

__version__ = "0.1.0"

__all__ = [
    "Decision",
    "RetryPolicy",
    "PolicyError",
    "decide",
    "parse_file",
    "parse_string",
    "__version__",
]
