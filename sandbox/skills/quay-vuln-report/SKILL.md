---
name: quay-vuln-report
description: Fetch fixable-vulnerability data (CVE, severity, fixed-in version, layer introduced, CVE link) for a public image on quay.io as JSON, via the quay.io REST API - never the quay.io web UI. Use when the user asks about vulnerabilities, CVEs, fixable/known-fixed issues, or security scan results for a quay.io image, or gives a quay.io repository/manifest link. Trigger keywords - quay.io vulnerabilities, CVE report, fixable, security scan, manifest security, rosa-agent image scan.
---

# quay.io fixable-vulnerability report

This skill wraps `quay_vuln_report`, a small stdlib-only Python CLI bundled
alongside this file (`./quay_vuln_report/`). It is specific to **quay.io**
and does not support Docker Hub, GHCR, ECR, or other registries.

It has **no pip/PyPI dependency**: every HTTP request is made by shelling
out to `/usr/bin/curl` rather than a Python HTTP library, specifically so it
works inside this sandbox's binary-scoped network policy - see
"Network policy" below before assuming a failure is a bug in the tool.

## Running it

From this skill's directory (or with an absolute path to it):

```bash
python3 -m quay_vuln_report --link "<a quay.io repository or manifest URL>"
# or
python3 -m quay_vuln_report --repository <namespace> --image <path> [--tag <tag>]
```

`--link` and `--repository`/`--image` are mutually exclusive. `--tag`
defaults to `latest`. Full flag reference: `python3 -m quay_vuln_report --help`.

Output is a single JSON object on stdout: image identity, a
`vulnerability_count`, and a `vulnerabilities` array with `cve`, `severity`,
`package`, `fixed_in_version`, `layer_introduced_in`, and `cve_link` per
entry. Non-fixable vulnerabilities are excluded by default (matches quay.io's
own `?fixable=true` UI filter) - pass `--include-non-fixable` to keep them.

Examples:

```bash
python3 -m quay_vuln_report --repository redhat-services-prod \
  --image rosa-tenant/rosa-agent/rosa-agent --tag latest

python3 -m quay_vuln_report --link "https://quay.io/repository/chcollin/dwarbot"
```

## Network policy

This skill needs the `quay_registry` block in `policies/default.yaml`
(read-only `quay.io:443`, binaries `curl`/`skopeo`/`claude`). Without it,
curl will fail with a policy-denied error and the tool surfaces that clearly
rather than hanging - the error text calls out that quay.io may not be
allow-listed. Do not try to work around a policy denial by calling the
quay.io API a different way (e.g. via `claude`'s own fetch, or asking the
user for a token) - this is a public, unauthenticated API; a denial here
means the policy isn't attached to this sandbox, not that access is
otherwise restricted.

If curl itself isn't at `/usr/bin/curl` in this image, pass
`--curl-bin <path>`.

## Do not "fix" this by using pip or the requests library

If something about this tool looks like it should use `pip install requests`
instead of shelling out to curl: don't. `pip` is not installed in this image
(no `python3-pip` in the Containerfile) and, separately, even a successfully
installed `requests` would still be blocked at runtime - the sandbox's
network policy allow-lists specific binary paths (`/usr/bin/curl`,
`/usr/bin/skopeo`, `/usr/bin/claude`, ...), not the `python3` interpreter
itself, so a raw socket opened from inside Python is denied even when curl
can reach the same host. This is why the tool shells out to curl for every
request. Keep it that way.
