---
name: pagerduty
description: Read PagerDuty incidents, alerts, and log entries, and read/create incident notes using curl against the PagerDuty REST API v2 in an OpenShell sandbox. Use when the user references a PagerDuty incident, alert, on-call page, or asks to look up or leave a note on an incident. Trigger keywords - pagerduty, incident, alert, on-call, page, notes, postmortem.
---

# PagerDuty REST API v2 in this sandbox

Credentials are pre-injected. Do NOT attempt interactive login, OAuth
client-secret flows, or ask the user for a token — the gateway already
mints and refreshes `PAGERDUTY_ACCESS_TOKEN` for you.

## Environment

| Variable | Meaning |
| --- | --- |
| `PAGERDUTY_ACCESS_TOKEN` | Scoped OAuth access token. Its value looks like an opaque credential placeholder (openshell resolve-env) — this is expected. Use it exactly as if it were a real Bearer token; the sandbox proxy substitutes the real value at egress, and the gateway refreshes it before it expires. Never try to decode, print, or work around it. |
| `PAGERDUTY_FROM_EMAIL` | A real PagerDuty user's email, required on note-creation requests only. May be unset if the caller only needs reads. |

## Required headers on every call

Unlike most REST APIs, PagerDuty needs two headers you must set explicitly
every time:

```shell
curl -sS \
  -H "Authorization: Bearer $PAGERDUTY_ACCESS_TOKEN" \
  -H "Accept: application/vnd.pagerduty+json;version=2" \
  "https://api.pagerduty.com/incidents"
```

- **`Authorization: Bearer $PAGERDUTY_ACCESS_TOKEN`** — do NOT use
  `Token token=...` here. That's PagerDuty's classic API-key scheme, and
  this sandbox's credential is a Scoped OAuth token, not a classic key —
  it only works as a Bearer token.
- **`Accept: application/vnd.pagerduty+json;version=2`** — required on
  every request, read or write. Omitting it can change response shape or
  be rejected outright.

Writes (note creation) need one more header:

- **`From: $PAGERDUTY_FROM_EMAIL`** — required only on
  `POST /incidents/{id}/notes`, identifying a real PagerDuty user for
  attribution. Not needed on reads. If `PAGERDUTY_FROM_EMAIL` is unset and
  you need to create a note, tell the user it must be set as a sandbox
  `--env` var — do not guess an email.

## What is allowed (proxy-enforced)

Reads:

```shell
# List incidents
curl -sS -H "Authorization: Bearer $PAGERDUTY_ACCESS_TOKEN" \
  -H "Accept: application/vnd.pagerduty+json;version=2" \
  "https://api.pagerduty.com/incidents"

# Get one incident
curl -sS -H "Authorization: Bearer $PAGERDUTY_ACCESS_TOKEN" \
  -H "Accept: application/vnd.pagerduty+json;version=2" \
  "https://api.pagerduty.com/incidents/$INCIDENT_ID"

# Incident activity/audit trail
curl -sS -H "Authorization: Bearer $PAGERDUTY_ACCESS_TOKEN" \
  -H "Accept: application/vnd.pagerduty+json;version=2" \
  "https://api.pagerduty.com/incidents/$INCIDENT_ID/log_entries"

# List alerts on an incident
curl -sS -H "Authorization: Bearer $PAGERDUTY_ACCESS_TOKEN" \
  -H "Accept: application/vnd.pagerduty+json;version=2" \
  "https://api.pagerduty.com/incidents/$INCIDENT_ID/alerts"

# Get a single alert (note: nested under the incident — there is no
# account-wide GET /alerts/{id} in PagerDuty's API)
curl -sS -H "Authorization: Bearer $PAGERDUTY_ACCESS_TOKEN" \
  -H "Accept: application/vnd.pagerduty+json;version=2" \
  "https://api.pagerduty.com/incidents/$INCIDENT_ID/alerts/$ALERT_ID"

# List notes on an incident
curl -sS -H "Authorization: Bearer $PAGERDUTY_ACCESS_TOKEN" \
  -H "Accept: application/vnd.pagerduty+json;version=2" \
  "https://api.pagerduty.com/incidents/$INCIDENT_ID/notes"
```

Writes — ONLY this operation is permitted:

```shell
# Create a note on an incident
curl -sS \
  -H "Authorization: Bearer $PAGERDUTY_ACCESS_TOKEN" \
  -H "Accept: application/vnd.pagerduty+json;version=2" \
  -H "From: $PAGERDUTY_FROM_EMAIL" \
  -H "Content-Type: application/json" \
  -X POST "https://api.pagerduty.com/incidents/$INCIDENT_ID/notes" \
  -d '{"note":{"content":"Findings here"}}'
```

## What is NOT allowed

Acknowledging, resolving, reassigning, or merging incidents; creating or
updating alerts; updating or deleting notes (PagerDuty has
`PUT`/`DELETE /incidents/{id}/notes/{note_id}` — this sandbox does not grant
either); escalation policies, schedules, services, users; and any other
write are denied by the sandbox network policy. A denied request returns
HTTP `403` (`policy_denied` or `credential_endpoint_mismatch`). If the
user's task truly requires a blocked operation, follow
`/etc/openshell/skills/policy_advisor.md` to propose the narrowest policy
addition and wait for approval — do not retry variations or attempt to
bypass the proxy.

## Troubleshooting auth (do NOT guess emails or bypass the proxy)

- **401/403 with `Token token=...`** — that scheme isn't supported through
  this sandbox at all; always use `Authorization: Bearer $PAGERDUTY_ACCESS_TOKEN`.
- **`{"error":{"message":"Token missing required scopes",...}}`** — the
  OAuth app or token wasn't granted the needed PagerDuty scope
  (`incidents.read`/`incidents.write`). This is a provider-configuration
  issue, not something fixable from inside the sandbox — report it.
- **400 on note creation** — almost always a missing/invalid `From` header.
  Confirm `PAGERDUTY_FROM_EMAIL` is set; never invent an email.
