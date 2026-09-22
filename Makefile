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

.PHONY: lint markdown-lint sandbox-build sandbox-push require-sandbox-image

# Parent lint target: runs every linter. The OpenShift CI `ci/prow/lint`
# presubmit runs `make lint`. Add new linters (shell, yaml, ...) as their own
# targets and list them as prerequisites here.
lint: markdown-lint

# Lint all Markdown docs. Requires markdownlint-cli2 (npm i -g markdownlint-cli2).
# Rules live in .markdownlint-cli2.yaml.
markdown-lint:
	markdownlint-cli2 "**/*.md"

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
# Konflux scheduled jobs (see README's "Konflux configuration > Scheduled
# jobs" section). Each CronJob's pod runs `make <job>` with
# OPENSHELL_OIDC_CLIENT_SECRET injected from Vault as an environment
# variable. Each job's Make logic — OpenShell gateway registration, OIDC
# token minting, and the one-shot sandbox create (--no-keep --no-tty) that
# runs the matching skill — lives in sandbox/skills/<job>.mk, next to that
# job's skill, and is pulled in below via `include` to keep it logically
# separate from the generic build/lint targets above.
# ---------------------------------------------------------------------------

include sandbox/skills/job-sop-improve.mk

# NOTE: there is no `sdlc-maturity` skill in this repo yet, so there is
# intentionally no `sdlc-maturity` target here even though the `sdlc-maturity`
# Konflux CronJob documented in the README calls `make sdlc-maturity`. Add
# one, following the `sop-improve` pattern (sandbox/skills/job-sop-improve.mk),
# once that skill exists under sandbox/skills/. See openshift-online/rosa-agent#1.
