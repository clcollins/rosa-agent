#!/usr/bin/env python3
"""Fail when tracked files contain raw credential-placeholder literals.

The OpenShell supervisor's request-body marker guard denies model requests
whose body contains ``openshell:resolve:env:`` or ``OPENSHELL-RESOLVE-ENV-``.
Claude Code loads skills and AGENTS.md into the request body, so these
literals must never appear in shipped content.

Containerfile is allowlisted because its build-time ``sed`` legitimately
references the pattern it is neutralising.

Exit codes:
    0  no violations found
    1  one or more violations found
"""

import os
import re
import subprocess
import sys

PATTERN = re.compile(r"openshell:resolve:env:|OPENSHELL-RESOLVE-ENV-", re.IGNORECASE)

ALLOWLISTED_PATHS = frozenset({"Containerfile"})


def find_violations(entries):
    """Return a list of (path, lineno, line) tuples for offending lines.

    *entries* is an iterable of ``(path, text)`` pairs where *path* is the
    repo-relative file path and *text* is its full content as a string.
    """
    findings = []
    for path, text in entries:
        if os.path.basename(path) in ALLOWLISTED_PATHS:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if PATTERN.search(line):
                findings.append((path, lineno, line.rstrip()))
    return findings


def _tracked_files(repo_root):
    """Yield (path, text) for every tracked file under *repo_root*."""
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    for relpath in result.stdout.split("\0"):
        if not relpath:
            continue
        abspath = os.path.join(repo_root, relpath)
        try:
            with open(abspath, encoding="utf-8", errors="surrogateescape") as fh:
                yield relpath, fh.read()
        except (OSError, UnicodeDecodeError):
            continue


def main(argv=None):
    args = argv if argv is not None else sys.argv[1:]
    repo_root = args[0] if args else os.getcwd()
    findings = find_violations(_tracked_files(repo_root))
    if findings:
        print("ERROR: raw credential-placeholder literals found:", file=sys.stderr)
        for path, lineno, line in findings:
            print(f"  {path}:{lineno}: {line}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
