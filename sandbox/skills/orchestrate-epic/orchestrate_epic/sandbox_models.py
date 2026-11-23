"""Pure functions for sandbox naming, command building, status parsing, and stuck detection."""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

MAX_SANDBOX_NAME_LENGTH = 19

_K8S_LABEL_KEY_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9_./-]*[A-Za-z0-9])?$")
_K8S_LABEL_VALUE_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9_.-]*[A-Za-z0-9])?$")
_MAX_LABEL_LENGTH = 63

_VALID_SANDBOX_TYPES = frozenset({"implementation", "fixup", "nit", "rebase"})


class SandboxNameError(ValueError):
    """Raised when a sandbox name is invalid."""


class SandboxLabelError(ValueError):
    """Raised when a sandbox label key or value is invalid."""


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
    "rebase": (20, 30),
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


def _validate_label(key: str, value: str) -> None:
    """Validate a single label key/value pair against Kubernetes rules."""
    for name, text, regex in (
        ("key", key, _K8S_LABEL_KEY_RE),
        ("value", value, _K8S_LABEL_VALUE_RE),
    ):
        if not text:
            raise SandboxLabelError(f"label {name} must not be empty")
        if len(text) > _MAX_LABEL_LENGTH:
            raise SandboxLabelError(
                f"label {name} '{text}' is {len(text)} chars, max {_MAX_LABEL_LENGTH}"
            )
        if not regex.match(text):
            raise SandboxLabelError(
                f"label {name} '{text}' contains invalid characters"
            )


def build_labels(
    run_id: str,
    epic_key: str,
    ticket_key: str,
    sandbox_type: str,
    environment: str = "test",
    extra: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """Build the standard cost-tracking label set for a child sandbox."""
    if sandbox_type not in _VALID_SANDBOX_TYPES:
        raise SandboxLabelError(
            f"sandbox_type must be one of {sorted(_VALID_SANDBOX_TYPES)}, "
            f"got '{sandbox_type}'"
        )

    labels: Dict[str, str] = {
        "project": "rosa-agent",
        "workload": "orchestrate-epic",
        "owner": "sd-sre-platform",
        "environment": environment,
        "run-id": run_id,
        "epic": epic_key.lower(),
        "ticket": ticket_key.lower(),
        "sandbox-type": sandbox_type,
    }

    if extra:
        for k, v in extra.items():
            if k in labels:
                raise SandboxLabelError(
                    f"extra label '{k}' conflicts with base/per-child label"
                )
            labels[k] = v

    for k, v in labels.items():
        _validate_label(k, v)

    return labels


def make_run_id(epic_key: str, now: datetime) -> str:
    """Generate a unique run ID from the epic key and a UTC timestamp."""
    run_id = f"{epic_key.lower()}-{now.strftime('%Y%m%d%H%M')}"
    if len(run_id) > _MAX_LABEL_LENGTH:
        raise SandboxLabelError(
            f"run_id '{run_id}' is {len(run_id)} chars, max {_MAX_LABEL_LENGTH}"
        )
    return run_id


def build_create_args(
    name: str,
    template_path: str,
    prompt: str,
    labels: Dict[str, str],
    model: Optional[str] = None,
) -> List[str]:
    """Build the argument list for openshellctl sandbox create.

    Returns the args AFTER 'openshellctl' — the caller prepends
    the binary name and vault env vars.
    """
    if not labels:
        raise SandboxLabelError("labels must not be empty")

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
    for k in sorted(labels):
        args.extend(["--label", f"{k}={labels[k]}"])
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
    """Determine if a sandbox is stuck based on type and elapsed time."""
    investigate_at, kill_at = _THRESHOLDS.get(
        sandbox_type, _THRESHOLDS["implementation"]
    )
    if elapsed_minutes >= kill_at:
        return StuckVerdict.KILL
    if elapsed_minutes >= investigate_at:
        return StuckVerdict.INVESTIGATE
    return StuckVerdict.OK
