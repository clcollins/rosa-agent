"""Command-line interface for orchestrate-epic helpers.

Provides subcommands for the deterministic parts of epic orchestration:
parsing Jira responses, building sandbox commands, and detecting stuck
sandboxes.  The orchestrating agent calls these as needed.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from . import __version__
from .jira_models import (
    JiraPayloadError,
    MergedStory,
    build_assignee_payload,
    build_epic_comment,
    build_remote_link_payload,
    build_sprint_payload,
    build_transition_payload,
    filter_actionable,
    parse_epic_children,
)
from .sandbox_models import (
    SandboxNameError,
    build_create_args,
    detect_stuck,
    sandbox_name,
    sandbox_name_suffixed,
)

PROG = "orchestrate-epic"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Deterministic helpers for sandbox-based epic orchestration.",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    sub = parser.add_subparsers(dest="command")

    p_filter = sub.add_parser(
        "filter-actionable",
        help="Read a Jira search response from stdin, emit actionable children as JSON.",
    )
    p_filter.set_defaults(func=cmd_filter_actionable)

    p_name = sub.add_parser(
        "sandbox-name",
        help="Derive a sandbox name from a ticket key.",
    )
    p_name.add_argument("ticket_key")
    p_name.add_argument("--suffix", default=None)
    p_name.set_defaults(func=cmd_sandbox_name)

    p_create = sub.add_parser(
        "sandbox-create-args",
        help="Emit the openshellctl sandbox create arg list as JSON.",
    )
    p_create.add_argument("--name", required=True)
    p_create.add_argument("--template", required=True)
    p_create.add_argument("--prompt", required=True)
    p_create.add_argument("--model", default=None)
    p_create.set_defaults(func=cmd_sandbox_create_args)

    p_stuck = sub.add_parser(
        "detect-stuck",
        help="Check if a sandbox is stuck.",
    )
    p_stuck.add_argument("--type", required=True, dest="sandbox_type")
    p_stuck.add_argument("--elapsed-minutes", required=True, type=int)
    p_stuck.set_defaults(func=cmd_detect_stuck)

    p_comment = sub.add_parser(
        "epic-comment",
        help="Build an epic completion comment. Read merged stories JSON from stdin.",
    )
    p_comment.add_argument("--repo", required=True)
    p_comment.set_defaults(func=cmd_epic_comment)

    p_assign = sub.add_parser(
        "assignee-payload",
        help="Emit a Jira assignee update payload.",
    )
    p_assign.add_argument("--account-id", required=True)
    p_assign.set_defaults(func=cmd_assignee_payload)

    p_link = sub.add_parser(
        "remote-link-payload",
        help="Emit a Jira remote link payload.",
    )
    p_link.add_argument("--url", required=True)
    p_link.add_argument("--title", required=True)
    p_link.set_defaults(func=cmd_remote_link_payload)

    p_trans = sub.add_parser(
        "transition-payload",
        help="Emit a Jira transition payload.",
    )
    p_trans.add_argument("--transition-id", required=True)
    p_trans.set_defaults(func=cmd_transition_payload)

    p_sprint = sub.add_parser(
        "sprint-payload",
        help="Emit a Jira sprint add-issues payload.",
    )
    p_sprint.add_argument("issues", nargs="+")
    p_sprint.set_defaults(func=cmd_sprint_payload)

    return parser


def cmd_filter_actionable(args: argparse.Namespace) -> int:
    data = json.load(sys.stdin)
    children = parse_epic_children(data)
    actionable = filter_actionable(children)
    result = [
        {"key": c.key, "summary": c.summary, "issue_type": c.issue_type}
        for c in actionable
    ]
    print(json.dumps(result, indent=2))
    return 0


def cmd_sandbox_name(args: argparse.Namespace) -> int:
    if args.suffix:
        name = sandbox_name_suffixed(args.ticket_key, args.suffix)
    else:
        name = sandbox_name(args.ticket_key)
    print(name)
    return 0


def cmd_sandbox_create_args(args: argparse.Namespace) -> int:
    result = build_create_args(
        name=args.name,
        template_path=args.template,
        prompt=args.prompt,
        model=args.model,
    )
    print(json.dumps(result))
    return 0


def cmd_detect_stuck(args: argparse.Namespace) -> int:
    verdict = detect_stuck(args.sandbox_type, args.elapsed_minutes)
    print(verdict.value)
    return 0


def cmd_epic_comment(args: argparse.Namespace) -> int:
    data = json.load(sys.stdin)
    stories = [MergedStory(**s) for s in data]
    comment = build_epic_comment(stories, repo=args.repo)
    print(comment)
    return 0


def cmd_assignee_payload(args: argparse.Namespace) -> int:
    print(json.dumps(build_assignee_payload(args.account_id)))
    return 0


def cmd_remote_link_payload(args: argparse.Namespace) -> int:
    print(json.dumps(build_remote_link_payload(args.url, args.title)))
    return 0


def cmd_transition_payload(args: argparse.Namespace) -> int:
    print(json.dumps(build_transition_payload(args.transition_id)))
    return 0


def cmd_sprint_payload(args: argparse.Namespace) -> int:
    print(json.dumps(build_sprint_payload(args.issues)))
    return 0


def run(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 1
    try:
        return args.func(args)
    except (JiraPayloadError, SandboxNameError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def main() -> None:
    sys.exit(run())
