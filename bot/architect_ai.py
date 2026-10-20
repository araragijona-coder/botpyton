"""Compatibility shim for the retired remote architecture adviser.

BOTPYTON's current policy is deterministic and offline for architecture advice.
This module deliberately contains no HTTP client, endpoint, key handling, or upload logic.
"""
from __future__ import annotations

from typing import Any


class ArchitectAIError(ValueError):
    """Raised when callers try to use the disabled remote adviser."""

    def __init__(self, message: str, *, network_used: bool = False) -> None:
        super().__init__(message)
        self.network_used = network_used


def request_architecture_advice(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Fail closed without inspecting inputs or touching the network."""
    raise ArchitectAIError(
        "External architecture advice is disabled by BOTPYTON's offline policy. "
        "Use --architect-plan for deterministic local planning.",
        network_used=False,
    )
