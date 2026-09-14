---
name: jira
description: Read Jira issues and add comments or web links using curl against the Jira Cloud REST API in an OpenShell sandbox. Use when the user references Jira issues, tickets, cards, sprints, boards, JQL, or asks to comment on or link something to a Jira issue. Trigger keywords - jira, ticket, issue key, JQL, sprint, backlog, board, comment on ticket.
---

# Jira Cloud in this sandbox

Credentials are pre-injected. Do NOT attempt interactive login, OAuth flows,
or ask the user for a token.

## Environment

| Variable | Meaning |
| --- | --- |
| `JIRA_API_TOKEN` | API token. Its value looks like an opaque `openshell:resolve:env:*` placeholder — this is expected. Use it exactly as if it were the real token; the sandbox proxy substitutes the real value at egress. Never try to decode, print, or work around it. |
| `JIRA_EMAIL` | Account email. Used ONLY for classic-token Basic auth. May be unset. |
| `JIRA_BASE_URL` | Site base URL. May be unset — if so, derive it (see below). |

**`JIRA_EMAIL` and `JIRA_BASE_URL` may be missing.** Do not fail if they are
unset — this skill derives everything it needs from `JIRA_API_TOKEN` plus the
tenant's cloudId. Never guess an email address; only use `JIRA_EMAIL` if it is
actually set.

## There are two Jira token types — you MUST pick the right one

Atlassian has two kinds of API token, and each requires a DIFFERENT host and
auth header. Using the wrong combination fails with 401/403 even though the
token is valid.

| Token type | Correct host | Auth header | Notes |
| --- | --- | --- | --- |
| **Scoped** (has scopes like `read:jira-work`) | `https://api.atlassian.com/ex/jira/<cloudId>` | `Authorization: Bearer $JIRA_API_TOKEN` | Fails on `*.atlassian.net`. Does NOT use email. |
| **Classic** (no scopes) | `https://<site>.atlassian.net` | Basic: `-u "$JIRA_EMAIL:$JIRA_API_TOKEN"` | Fails on `api.atlassian.com`. Requires `JIRA_EMAIL`. |

You cannot read the token to tell which type it is. **Detect it empirically**
by probing `/rest/api/3/myself` with each mode and using whichever returns
HTTP 200. Run the setup block below ONCE at the start of any Jira work, then
reuse `$JB` (base URL) and the `jira` shell function for every call.

## Setup: detect auth mode (run this first, every session)

```shell
# 1. Determine the tenant cloudId and the scoped base URL.
#    Fast path: if JIRA_BASE_URL is already the scoped
#    api.atlassian.com/ex/jira/<cloudId> form (the recommended provider
#    config), use it directly and make NO discovery call.
CID=""; SCOPED_BASE=""
case "${JIRA_BASE_URL:-}" in
  https://api.atlassian.com/ex/jira/*)
    SCOPED_BASE="${JIRA_BASE_URL%/}"
    CID="${SCOPED_BASE##*/ex/jira/}"; CID="${CID%%/*}"
    ;;
esac

# Site host for classic-token Basic auth (and discovery fallback only).
_site="${JIRA_BASE_URL:-https://redhat.atlassian.net}"
case "$_site" in
  https://api.atlassian.com*) _site="https://redhat.atlassian.net" ;;
esac

# Fallback: derive cloudId from the tenant_info discovery endpoint ONLY when it
# was not supplied via JIRA_BASE_URL. This endpoint may be denied by the
# provider egress policy; if it is, set JIRA_BASE_URL to the scoped form
# (api.atlassian.com/ex/jira/<cloudId>) on the provider instead of relying on
# discovery.
if [ -z "$CID" ]; then
  CID="$(curl -sS "$_site/_edge/tenant_info" | python3 -c 'import sys,json; print(json.load(sys.stdin)["cloudId"])' 2>/dev/null || true)"
fi
[ -z "$SCOPED_BASE" ] && [ -n "$CID" ] && SCOPED_BASE="https://api.atlassian.com/ex/jira/$CID"

# 2. Probe scoped (Bearer @ api.atlassian.com) first, then classic (Basic @ site).
JB=""; JAUTH=""
if [ -n "$SCOPED_BASE" ] && [ "$(curl -sS -o /dev/null -w '%{http_code}' \
      -H "Authorization: Bearer $JIRA_API_TOKEN" \
      "$SCOPED_BASE/rest/api/3/myself")" = "200" ]; then
  JB="$SCOPED_BASE"
  JAUTH="bearer"
elif [ -n "$JIRA_EMAIL" ] && [ "$(curl -sS -o /dev/null -w '%{http_code}' \
      -u "$JIRA_EMAIL:$JIRA_API_TOKEN" \
      "$_site/rest/api/3/myself")" = "200" ]; then
  JB="$_site"
  JAUTH="basic"
else
  echo "ERROR: Jira auth failed for both scoped (Bearer) and classic (Basic) modes." >&2
  echo "Scoped needs api.atlassian.com/ex/jira/<cloudId>; classic needs JIRA_EMAIL set." >&2
  echo "If cloudId discovery (_edge/tenant_info) is policy-denied, set JIRA_BASE_URL" >&2
  echo "to the scoped api.atlassian.com/ex/jira/<cloudId> form on the provider." >&2
  return 1 2>/dev/null || exit 1
fi
echo "Jira auth mode: $JAUTH  base: $JB"

# 3. Define a helper that applies the detected auth to every request.
jira() {  # usage: jira <curl-args...> <path-relative-to-JB>
  if [ "$JAUTH" = "bearer" ]; then
    curl -sS -H "Authorization: Bearer $JIRA_API_TOKEN" "$@"
  else
    curl -sS -u "$JIRA_EMAIL:$JIRA_API_TOKEN" "$@"
  fi
}
```

After this runs, `$JB` is the correct base URL and `jira ...` carries the
correct auth. Build every API URL as `"$JB/rest/api/3/..."`.

## How to call the API

Always go through the `jira` helper and `$JB` from the setup block:

```shell
jira -H "Accept: application/json" "$JB/rest/api/3/issue/PROJ-123"
```

## What is allowed (proxy-enforced)

Reads: any `GET` under `/rest/api/3/**` and `/rest/agile/1.0/**`, plus
`POST /rest/api/3/search/jql` for JQL search, plus
`GET /_edge/tenant_info` for cloudId discovery (used by the setup block).

Writes — ONLY these two operations are permitted:

1. Create a comment:

```shell
jira -H "Content-Type: application/json" \
  -X POST "$JB/rest/api/3/issue/PROJ-123/comment" \
  -d '{"body":{"type":"doc","version":1,"content":[{"type":"paragraph","content":[{"type":"text","text":"Comment text here"}]}]}}'
```

Comment bodies use Atlassian Document Format (ADF), not plain strings.

1. Create a remote (web) link on an issue:

```shell
jira -H "Content-Type: application/json" \
  -X POST "$JB/rest/api/3/issue/PROJ-123/remotelink" \
  -d '{"object":{"url":"https://example.com/page","title":"Link title"}}'
```

## Useful read patterns

```shell
# JQL search (POST; fields param trims the response)
jira -H "Content-Type: application/json" \
  -X POST "$JB/rest/api/3/search/jql" \
  -d '{"jql":"project = PROJ AND status = \"In Progress\" ORDER BY updated DESC","fields":["summary","status","assignee"],"maxResults":25}'

# Get a single issue
jira "$JB/rest/api/3/issue/PROJ-123"

# Comments on an issue
jira "$JB/rest/api/3/issue/PROJ-123/comment"

# Boards (Agile API)
jira "$JB/rest/agile/1.0/board"

# Sprints for a board
jira "$JB/rest/agile/1.0/board/{boardId}/sprint?state=active"
```

## What is NOT allowed

Editing issues, transitions, deletes, editing or deleting comments,
issue-to-issue links, and any other mutation are denied by the sandbox
network policy. A denied request returns HTTP 403 (`policy_denied` or
`credential_endpoint_mismatch`). If the user's task truly requires a
blocked operation, follow `/etc/openshell/skills/policy_advisor.md` to
propose the narrowest policy addition and wait for approval — do not
retry variations or attempt to bypass the proxy.

## Troubleshooting auth (do NOT brute-force emails or tokens)

- **401 on `*.atlassian.net` with Basic** and you have a scoped token → wrong
  host/auth. Use Bearer against `api.atlassian.com/ex/jira/<cloudId>`. The
  setup block already handles this; re-run it.
- **403 on `api.atlassian.com` with Bearer** → the token is classic, not
  scoped. Use Basic against the `*.atlassian.net` site with `JIRA_EMAIL`.
- **Both modes fail** → the credential or `JIRA_EMAIL` is misconfigured on the
  provider. Report the exact HTTP codes and stop; do not guess emails or
  attempt to read/decode the token.
