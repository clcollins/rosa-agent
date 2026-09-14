# Sandbox agent operating constraints

You are running in an OpenShell sandbox with pre-injected credentials and a
proxy-enforced egress policy. These rules are mandatory and are read on every
turn. Skills teach you the correct procedure; these rules make following them
non-optional.

IMPORTANT: When working within repositories, you must fork the repository
(or clone your fork if it exists), check out a feature branch based off of the
repository's default branch to ensure it's up to date, and create Pull Requests
to the upstream repository with your work.

## Credentials are pre-injected — never authenticate interactively

Tokens appear as opaque `openshell:resolve:env:*` placeholders. Use them
verbatim; the proxy substitutes the real value at egress. You MUST NOT:

- run `gh auth login`, OAuth flows, or any interactive login;
- ask the user for a token, password, or email;
- try to decode, print, or work around a placeholder value.

## Jira — you MUST use the `jira` skill

Red Hat Jira is Atlassian **Cloud**. The ONLY valid Jira hosts are
`redhat.atlassian.net` and `api.atlassian.com`. There is NO `issues.redhat.com`,
`jira.redhat.com`, `api.redhat.com`, or any `*.redhat.com` Jira — never probe,
CONNECT to, or use those hosts. Scoped tokens use
`https://api.atlassian.com/ex/jira/<cloudId>`; classic tokens use
`https://redhat.atlassian.net`. The `jira` skill detects which.

For ANY task involving Jira — an issue key (e.g. `ROSAENG-62312`), a ticket,
card, comment, web link, JQL query, board, or sprint — you MUST load and
follow the `jira` skill BEFORE making any request. Do not improvise Jira calls.

- Never construct a Jira host, base URL, cloudId, or auth header by guessing.
- Never probe alternate hosts to "find" Jira — the two hosts above are the
  only ones; if they are denied, report it, do not search for others.
- Never brute-force, infer, or invent an email address.
- Run the skill's auth-detection block ONCE, then use `$JB` and the `jira`
  helper function for every call.
- Only reads, comment-create, and remotelink-create are permitted. An HTTP
  `403` means the operation is policy-denied — report it and stop; do not
  retry variations or attempt to bypass the proxy.
- If both auth modes fail, report the exact HTTP codes and stop. Do not guess.

## GitHub — you MUST use the `github` skill

For git/GitHub work, follow the `github` skill. Clone with the injected
`GITHUB_TOKEN` placeholder (`gh repo clone org/repo`, or
`git clone https://x-access-token:${GITHUB_TOKEN}@github.com/org/repo`).
Never run `gh auth login`. GraphQL is blocked — use REST (`gh api`) only.

## Egress is allow-listed

Requests to undeclared hosts, methods, or paths are denied at the proxy
(HTTP `403`, `policy_denied`, or `credential_endpoint_mismatch`). If a task
genuinely requires a blocked operation, propose the narrowest possible policy
addition and stop for approval — do not attempt to reach the endpoint another
way or bypass enforcement.
