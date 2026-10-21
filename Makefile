CONTAINER_ENGINE ?= podman

# Fully-qualified sandbox image WITHOUT tag, e.g. quay.io/<org>/rosa-agent.
# Intentionally unset by default: there is no shared registry baked in, so a
# push must be given one explicitly (SANDBOX_IMAGE=... make sandbox-push).
SANDBOX_IMAGE ?=

# The tag is the short git SHA so every push is immutable and pods can pin an
# exact build; `latest` is also pushed as a moving convenience tag.
SANDBOX_TAG ?= $(shell git rev-parse --short=7 HEAD)
SANDBOX_CONTAINERFILE := Containerfile
SANDBOX_CONTEXT := .

.PHONY: lint markdown-lint check-placeholders lint-sandbox test test-hack sandbox-build sandbox-push require-sandbox-image

# Parent lint target: runs every linter. The OpenShift CI `ci/prow/lint`
# presubmit runs `make lint`. Add new linters (shell, yaml, ...) as their own
# targets and list them as prerequisites here.
lint: markdown-lint check-placeholders lint-sandbox

# Lint all Markdown docs. Requires markdownlint-cli2 (npm i -g markdownlint-cli2).
# Rules live in .markdownlint-cli2.yaml.
markdown-lint:
	markdownlint-cli2 "**/*.md"

# Fail if any tracked file contains raw credential-placeholder literals
# that trip the supervisor's request-body marker guard.  Containerfile
# and the checker itself are allowlisted.
check-placeholders:
	python3 hack/check_placeholder_literals.py

# Guard against re-introduction of removed Bedrock/inference.local config.
# Covers sandbox files, docs, provider profiles, and policies. See MCS-324.
SANDBOX_FORBIDDEN := inference\.local|ANTHROPIC_API_KEY|ANTHROPIC_BASE_URL|rosa-general-vertex|vertex-a([^i]|$$)
SANDBOX_LINT_PATHS := sandbox/templates/ 'sandbox/skills/**/*-cron.yaml' README.md provider-profiles/ policies/ docs/
lint-sandbox:
	@git grep --extended-regexp --line-number '$(SANDBOX_FORBIDDEN)' -- $(SANDBOX_LINT_PATHS); \
	rc=$$?; \
	if [ $$rc -eq 0 ]; then echo "ERROR: forbidden Bedrock-era config found (MCS-324)"; exit 1; fi; \
	if [ $$rc -ne 1 ]; then echo "ERROR: git grep failed (rc=$$rc)"; exit $$rc; fi; \
	echo "lint-sandbox: OK"

# Run every skill's Python unit tests, then any bundled shell-script tests.
# Stdlib unittest only - this image has no pip, so no pytest/coverage
# tooling is available or expected. Each skill's own directory is put on
# sys.path (via unittest's -t), matching how `python3 -m <package>` already
# resolves imports for that skill. Shell-script tests (no bats/shunit2 in
# this image) are plain executable scripts under scripts/tests/ that report
# their own pass/fail and exit non-zero on failure.
test:
	@set -e; for d in sandbox/skills/*/; do \
		if compgen -G "$${d}tests/test_*.py" > /dev/null; then \
			echo "=== $$d ==="; \
			(cd "$$d" && python3 -m unittest discover -s tests -t .); \
		fi; \
		if compgen -G "$${d}scripts/tests/test_*.sh" > /dev/null; then \
			for t in "$$d"scripts/tests/test_*.sh; do \
				echo "=== $$t ==="; \
				bash "$$t"; \
			done; \
		fi; \
	done
	$(MAKE) test-hack

# Unit tests for Python scripts in hack/.
test-hack:
	@echo "=== hack/tests ==="; \
	PYTHONPATH=hack python3 -m unittest discover -s hack/tests

# Local build tag when no image is given, so `make sandbox-build` works without
# a registry. A push always requires SANDBOX_IMAGE (see require-sandbox-image).
SANDBOX_LOCAL_IMAGE := rosa-agent
SANDBOX_BUILD_IMAGE := $(if $(SANDBOX_IMAGE),$(SANDBOX_IMAGE),$(SANDBOX_LOCAL_IMAGE))

sandbox-build:
	$(CONTAINER_ENGINE) build \
		--tag $(SANDBOX_BUILD_IMAGE):$(SANDBOX_TAG) \
		--tag $(SANDBOX_BUILD_IMAGE):latest \
		--file $(SANDBOX_CONTAINERFILE) \
		$(SANDBOX_CONTEXT)

require-sandbox-image:
	@test -n "$(SANDBOX_IMAGE)" || { \
		echo "ERROR: SANDBOX_IMAGE is not set; refusing to push."; \
		echo "Set a fully-qualified image, e.g. SANDBOX_IMAGE=quay.io/<org>/rosa-agent make sandbox-push"; \
		exit 1; \
	}

sandbox-push: require-sandbox-image sandbox-build
	$(CONTAINER_ENGINE) push $(SANDBOX_IMAGE):$(SANDBOX_TAG)
	$(CONTAINER_ENGINE) push $(SANDBOX_IMAGE):latest
	@echo "Pushed $(SANDBOX_IMAGE):$(SANDBOX_TAG)"

# ---------------------------------------------------------------------------
# Scheduled jobs
#
# CronJobs are deployed directly to the rosaeng cluster (not via Konflux)
# because Konflux build clusters cannot reach the Hypershell OIDC endpoint.
# Each CronJob's manifest lives alongside its job skill and contains the
# full inline script (gateway registration, OIDC token mint, sandbox
# create):
#   sandbox/skills/job-sop-improve/job-sop-improve-cron.yaml
#   sandbox/skills/job-image-vuln-check/job-image-vuln-check-cron.yaml
#   sandbox/skills/job-ops-sop-pr-review/job-ops-sop-pr-review-cron.yaml
# Apply each manually with `oc apply -f` plus the openshell-oidc secret.
# ---------------------------------------------------------------------------
