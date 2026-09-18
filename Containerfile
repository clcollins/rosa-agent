# ---- Stage 1: binary downloads with SHA256 verification ----
FROM registry.access.redhat.com/ubi9/ubi:9.8-1789646010 AS builder

ARG TARGETARCH
ARG GH_VERSION=2.58.0
ARG GOLANGCI_LINT_VERSION=2.7.2
ARG STATICCHECK_VERSION=2025.1.1
ARG SHELLCHECK_VERSION=0.10.0
ARG GLAB_VERSION=1.118.0

RUN set -eux; \
    dnf -y install --setopt=install_weak_deps=False --nodocs \
        curl-minimal ca-certificates tar gzip xz; \
    ARCH="${TARGETARCH:-amd64}"; \
    # --- gh (GitHub CLI) ---
    curl -fsSLo /tmp/gh.tar.gz \
      "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_${ARCH}.tar.gz"; \
    tar -C /tmp -xzf /tmp/gh.tar.gz; \
    install -m0755 "/tmp/gh_${GH_VERSION}_linux_${ARCH}/bin/gh" /usr/local/bin/gh; \
    # --- golangci-lint ---
    curl -fsSLo /tmp/golangci-lint.tar.gz \
      "https://github.com/golangci/golangci-lint/releases/download/v${GOLANGCI_LINT_VERSION}/golangci-lint-${GOLANGCI_LINT_VERSION}-linux-${ARCH}.tar.gz"; \
    tar -C /tmp -xzf /tmp/golangci-lint.tar.gz; \
    install -m0755 "/tmp/golangci-lint-${GOLANGCI_LINT_VERSION}-linux-${ARCH}/golangci-lint" /usr/local/bin/golangci-lint; \
    # --- staticcheck ---
    curl -fsSLo /tmp/staticcheck.tar.gz \
      "https://github.com/dominikh/go-tools/releases/download/${STATICCHECK_VERSION}/staticcheck_linux_${ARCH}.tar.gz"; \
    tar -C /tmp -xzf /tmp/staticcheck.tar.gz; \
    install -m0755 /tmp/staticcheck/staticcheck /usr/local/bin/staticcheck; \
    # --- shellcheck ---
    SC_ARCH="${ARCH}"; \
    if [ "${SC_ARCH}" = "amd64" ]; then SC_ARCH="x86_64"; fi; \
    if [ "${SC_ARCH}" = "arm64" ]; then SC_ARCH="aarch64"; fi; \
    curl -fsSLo /tmp/shellcheck.tar.xz \
      "https://github.com/koalaman/ShellCheck/releases/download/v${SHELLCHECK_VERSION}/shellcheck-v${SHELLCHECK_VERSION}.linux.${SC_ARCH}.tar.xz"; \
    tar -C /tmp -xJf /tmp/shellcheck.tar.xz; \
    install -m0755 "/tmp/shellcheck-v${SHELLCHECK_VERSION}/shellcheck" /usr/local/bin/shellcheck; \
    # --- glab (GitLab CLI) ---
    # No UBI/EPEL rpm exists for glab; install the same way as the other
    # GitHub-hosted tools above, from GitLab's own release tarballs.
    curl -fsSLo /tmp/glab.tar.gz \
      "https://gitlab.com/gitlab-org/cli/-/releases/v${GLAB_VERSION}/downloads/glab_${GLAB_VERSION}_linux_${ARCH}.tar.gz"; \
    tar -C /tmp -xzf /tmp/glab.tar.gz; \
    install -m0755 /tmp/bin/glab /usr/local/bin/glab; \
    # --- cleanup ---
    rm -rf /tmp/*

# --- Claude Code signed repo ---
RUN set -eux; \
    dnf -y install --nodocs 'dnf-command(config-manager)'; \
    printf '%s\n' \
      '[claude-code]' \
      'name=Claude Code' \
      'baseurl=https://downloads.claude.ai/claude-code/rpm/stable' \
      'enabled=1' \
      'gpgcheck=1' \
      'gpgkey=https://downloads.claude.ai/keys/claude-code.asc' \
      > /etc/yum.repos.d/claude-code.repo

RUN set -eux; \
    dnf -y install --nodocs claude-code

# ---- Stage 2: final sandbox image ----
FROM registry.access.redhat.com/ubi9/ubi:9.8-1789646010

LABEL org.opencontainers.image.title="openshell-sandbox-go" \
      org.opencontainers.image.description="Go operator development sandbox for OpenShell gateway" \
      org.opencontainers.image.base.name="registry.access.redhat.com/ubi9/ubi:9.8"

# --- system packages + Go toolset + Claude Code ---
RUN set -eux; \
    dnf -y install --setopt=install_weak_deps=False --nodocs \
        git openssh-clients ca-certificates \
        go-toolset \
        python3 \
        jq make gcc findutils which tar gzip diffutils \
        curl-minimal rsync procps-ng \
        npm; \
    dnf clean all; rm -rf /var/cache/dnf

# --- markdownlint (for documentation review by sub-agents) ---
RUN npm install --global markdownlint-cli2@0.17.2 \
    && npm cache clean --force

# --- binaries from builder stage ---
COPY --from=builder /usr/local/bin/gh /usr/local/bin/gh
COPY --from=builder /usr/local/bin/golangci-lint /usr/local/bin/golangci-lint
COPY --from=builder /usr/local/bin/staticcheck /usr/local/bin/staticcheck
COPY --from=builder /usr/local/bin/shellcheck /usr/local/bin/shellcheck
COPY --from=builder /usr/local/bin/glab /usr/local/bin/glab
COPY --from=builder /usr/bin/claude /usr/bin/claude

# --- Go tools via go install (pinned versions, matching boilerplate) ---
RUN set -eux; \
    export GOPATH=/go; \
    export GOFLAGS=-mod=mod; \
    go install sigs.k8s.io/controller-tools/cmd/controller-gen@v0.16.4; \
    go install k8s.io/code-generator/cmd/openapi-gen@v0.29.15; \
    go install go.uber.org/mock/mockgen@v0.4.0; \
    go install golang.org/x/vuln/cmd/govulncheck@v1.1.4; \
    go install sigs.k8s.io/controller-runtime/tools/setup-envtest@release-0.23; \
    # Fix GOPATH permissions for OpenShift arbitrary UID (GID 0 pattern)
    for bit in r x; do \
      find /go -perm -u+${bit} -a ! -perm -g+${bit} -exec chmod g+${bit} {} +; \
    done; \
    rm -rf /tmp/*

# --- user model (mirrors NVIDIA OpenShell-Community base sandbox) ---
# HOME=/sandbox matches the NVIDIA base image pattern. The workspace-init
# init container seeds /sandbox from the image into the PVC on first use,
# so files baked here (including .claude.json) are available at runtime.
RUN set -eux; \
    groupadd -r supervisor; \
    useradd -r -g supervisor -s /usr/sbin/nologin supervisor; \
    groupadd -r sandbox; \
    useradd -r -g sandbox -d /sandbox -s /bin/bash sandbox; \
    mkdir -p /sandbox /tmp/sandbox-cache; \
    chown -R sandbox:sandbox /sandbox /tmp/sandbox-cache

# --- directory structure ---
RUN set -eux; \
    mkdir -p /etc/openshell \
             /sandbox/.claude \
             /sandbox/.config \
             /sandbox/.cache/go-build; \
    chown -R sandbox:sandbox /sandbox

# --- agent skills ---
# Copy NVIDIA base image skills (e.g. github/SKILL.md) then add our own.
# Skills are discovered from /sandbox/.agents/skills/ and symlinked into
# /sandbox/.claude/skills/ for Claude Code discovery. The NVIDIA base image
# ships the github skill teaching agents to use gh api REST-only (GraphQL
# is blocked by the sandbox network policy).
COPY --from="ghcr.io/nvidia/openshell-community/sandboxes/base:latest" /sandbox/.agents/skills/ /sandbox/.agents/skills/
COPY sandbox/skills/ /sandbox/.agents/skills/
RUN set -eux; \
    rm -rf /sandbox/.claude/skills; \
    ln -sf /sandbox/.agents/skills /sandbox/.claude/skills; \
    chown -R sandbox:sandbox /sandbox/.agents/skills

# --- sandbox operating constraints (always-loaded agent instructions) ---
# Read from HOME=/sandbox on every turn. This is the forcing layer: skills are
# advisory (the model may skip them), so these instructions make skill use
# mandatory and prohibit guessing Jira/GitHub auth, hosts, or emails. AGENTS.md
# holds the content (tool-neutral); CLAUDE.md is a thin @AGENTS.md include,
# mirroring the repository root convention so Claude Code auto-discovers it.
COPY sandbox/AGENTS.md /sandbox/AGENTS.md
COPY sandbox/CLAUDE.md /sandbox/CLAUDE.md
RUN chown sandbox:sandbox /sandbox/AGENTS.md /sandbox/CLAUDE.md

# --- default sandbox policy ---
# Baked at the OpenShell-standard path so it applies automatically when
# --policy is not passed at sandbox create time. Precedence (lowest to highest):
#   /etc/openshell/policy.yaml (this file) < OPENSHELL_SANDBOX_POLICY < --policy flag
# Operators override per-sandbox with --policy, or gateway-wide via the env var.
COPY policies/default.yaml /etc/openshell/policy.yaml

# --- pre-seed Claude Code trust state ---
# Baked into /sandbox/.claude.json — the workspace-init init container seeds
# /sandbox from the image into the PVC on first use. Claude Code reads
# ~/.claude.json (HOME=/sandbox) so this path is correct. On first sandbox
# creation with a given name the PVC gets seeded from the image including this
# file. On subsequent runs the PVC already contains it.
#
# Pre-approves everything that would otherwise block a non-interactive
# `--dangerously-skip-permissions --print` run:
#   - bypassPermissionsModeAccepted: enables --dangerously-skip-permissions
#   - customApiKeyResponses.approved ["unused"]: matches ANTHROPIC_API_KEY=unused
#     so the custom-API-key approval prompt is suppressed
#   - projects./sandbox trust + hooks: accepts the workspace trust dialogs
RUN printf '%s\n' \
      '{' \
      '  "hasCompletedOnboarding": true,' \
      '  "bypassPermissionsModeAccepted": true,' \
      '  "theme": "dark",' \
      '  "customApiKeyResponses": {' \
      '    "approved": ["unused"],' \
      '    "rejected": []' \
      '  },' \
      '  "projects": {' \
      '    "/sandbox": {' \
      '      "hasTrustDialogAccepted": true,' \
      '      "hasTrustDialogHooksAccepted": true' \
      '    }' \
      '  }' \
      '}' > /sandbox/.claude.json \
    && chown sandbox:sandbox /sandbox/.claude.json

# --- environment ---
ENV PATH="/usr/local/go/bin:/go/bin:/sandbox/.local/bin:/usr/local/bin:/usr/bin:/bin" \
    GOPATH=/go \
    GOCACHE=/sandbox/.cache/go-build \
    GOMODCACHE=/go/pkg/mod \
    GOTOOLCHAIN=auto \
    XDG_CACHE_HOME=/tmp/sandbox-cache \
    HOME=/sandbox \
    DISABLE_AUTOUPDATER=1 \
    DISABLE_UPDATES=1

# --- agent runtime defaults ---
# Baked defaults for Bedrock-routed Claude Code and Jira Cloud. Per-sandbox
# overrides can still be passed via `openshell sandbox create --env`.
#
# Note: these do not appear to continue to exist inside the container.
# The openshell scaffolding removes environment variables before injecting its own?

ENV ANTHROPIC_MODEL=us.anthropic.claude-sonnet-4-5-20250929-v1:0 \
    ANTHROPIC_BASE_URL=https://inference.local \
    ANTHROPIC_API_KEY=unused \
    JIRA_EMAIL="sd-sre-platform+rosa-agent@redhat.com" \
    JIRA_BASE_URL="https://redhat.atlassian.net"

USER sandbox
WORKDIR /sandbox
