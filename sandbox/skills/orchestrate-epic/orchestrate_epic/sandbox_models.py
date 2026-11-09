"""Pure functions for sandbox naming, command building, status parsing, and stuck detection."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

MAX_SANDBOX_NAME_LENGTH = 19


class SandboxNameError(ValueError):
    """Raised when a sandbox name is invalid."""


class StuckVerdict(enum.Enum):
    OK = "ok"
    INVESTIGATE = "investigate"
    KILL = "kill"


@dataclass(frozen=True)
class SandboxStatus:
    name: str
    phase: str
    resource_version: int


_THRESHOLDS = {
    "nit": (15, 20),
    "fixup": (20, 30),
    "implementation": (60, 90),
}


def sandbox_name(ticket_key: str) -> str:
    """Derive a sandbox name from a Jira ticket key."""
    name = ticket_key.strip().lower()
    if not name:
        raise SandboxNameError("ticket key must not be empty")
    if len(name) > MAX_SANDBOX_NAME_LENGTH:
        raise SandboxNameError(
            f"sandbox name '{name}' is {len(name)} chars, max {MAX_SANDBOX_NAME_LENGTH}"
        )
    return name


def sandbox_name_suffixed(ticket_key: str, suffix: str) -> str:
    """Derive a sandbox name with a suffix (e.g., 'fixup', 'nits', 'rebase')."""
    if not suffix:
        raise SandboxNameError("suffix must not be empty")
    combined = f"{ticket_key.strip().lower()}-{suffix.strip().lower()}"
    if len(combined) > MAX_SANDBOX_NAME_LENGTH:
        raise SandboxNameError(
            f"sandbox name '{combined}' is {len(combined)} chars, max {MAX_SANDBOX_NAME_LENGTH}"
        )
    return combined


def build_create_args(
    name: str,
    template_path: str,
    prompt: str,
    model: Optional[str] = None,
) -> List[str]:
    """Build the argument list for openshellctl sandbox create.

    Returns the args AFTER 'openshellctl' — the caller prepends
    the binary name and vault env vars.
    """
    args = [
        "sandbox",
        "create",
        "--name",
        name,
        "-f",
        template_path,
        "-p",
        f"NAME={name}",
        "-p",
        f"PROMPT={prompt}",
        "--detach",
        "--replace",
    ]
    if model:
        args.extend(["--env", f"ANTHROPIC_MODEL={model}"])
    return args


def parse_sandbox_status(data: Dict[str, Any]) -> SandboxStatus:
    """Parse a single sandbox JSON object from openshellctl sandbox get --output json."""
    return SandboxStatus(
        name=data["metadata"]["name"],
        phase=data["phase"],
        resource_version=data.get("resource_version", 0),
    )


def parse_sandbox_list(data: List[Dict[str, Any]]) -> List[SandboxStatus]:
    """Parse a list of sandbox JSON objects from openshellctl sandbox list --output json."""
    return [parse_sandbox_status(item) for item in data]


def detect_stuck(sandbox_type: str, elapsed_minutes: int) -> StuckVerdict:
    """Determine if a sandbox is stuck based on type and elapsed time.

    sandbox_type: one of 'nit', 'fixup', 'implementation'.
    Falls back to 'implementation' thresholds for unknown types.
    """
    investigate_at, kill_at = _THRESHOLDS.get(
        sandbox_type, _THRESHOLDS["implementation"]
    )
    if elapsed_minutes >= kill_at:
        return StuckVerdict.KILL
    if elapsed_minutes >= investigate_at:
        return StuckVerdict.INVESTIGATE
    return StuckVerdict.OK
