"""Conservative recovery registry. Unknown failures are never auto-repaired."""
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class RecoveryDecision:
    category: str
    strategy: str | None
    retryable: bool
    reason: str

# Exact, intentionally small signatures; add rules only with regression tests.
KNOWN_RULES = (
    ("ModuleNotFoundError", "test_environment", "verify_declared_dependencies"),
    ("SyntaxError", "source_syntax", "inspect_syntax_location"),
    ("JSONDecodeError", "invalid_json", "validate_json_schema"),
    ("CONFLICT (content)", "git_conflict", "rebuild_isolated_branch"),
)

def classify_failure(log_text: str, *, attempt: int = 1, max_attempts: int = 3) -> RecoveryDecision:
    if attempt < 1 or max_attempts < 1 or attempt > max_attempts:
        return RecoveryDecision("attempt_limit", None, False, "Attempt count is outside configured bounds")
    for signature, category, strategy in KNOWN_RULES:
        if signature in log_text:
            return RecoveryDecision(category, strategy, attempt < max_attempts,
                                    f"Known signature matched: {signature}")
    return RecoveryDecision("unknown", None, False, "No exact known-error rule matched; stop and report")
