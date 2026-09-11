#!/usr/bin/env python3
"""Refresh an OpenShell gateway's oidc_token.json in place.

Mints a fresh access token via the OIDC client_credentials grant and writes it
into the gateway's oidc_token.json atomically, merging into any existing fields
so nothing the OpenShell CLI stored gets dropped.

The service-account client secret is long-lived; the access token is not. There
is no refresh_token with client_credentials, so "refresh" == re-mint. Run this
whenever the token is near expiry, on demand, or via --exec to wrap a command
with automatic re-mint on auth failure.

Config resolves in priority order: CLI flags > environment > metadata.json.
The client secret is read from OPENSHELL_OIDC_CLIENT_SECRET; ONLY if that env
var is unset does it fall back to Vault. The Vault fallback uses the HTTP API
(KV v2) with the token from $VAULT_TOKEN or ~/.vault-token; if that token is
missing or rejected, it runs 'vault login -method=oidc' (opening a browser, or
printing the auth URL with --no-browser) and retries.

Examples:
    # Just refresh the token file:
    ./refresh_openshell_token.py -g 'ROSA Agentic Devx-rosa-agent'

    # Refresh only if fewer than 90s of life remain:
    ./refresh_openshell_token.py -g 'ROSA Agentic Devx-rosa-agent' --if-expiring 90

    # Refresh (as needed) then run a command, re-minting once on auth failure:
    ./refresh_openshell_token.py -g 'ROSA Agentic Devx-rosa-agent' \
        --exec -- sandbox create --name demo
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


DEFAULT_VAULT_MOUNT = "osd-sre"
DEFAULT_VAULT_PATH = "rosa-agent"
DEFAULT_VAULT_FIELD = "hypershell-oidc-client-secret"

# Substrings that indicate the CLI failed because the token was rejected.
# The upstream OpenShell gateway (NVIDIA/OpenShell) returns gRPC
# Code::Unauthenticated with a message that always contains "invalid token"
# (e.g. "invalid token: ExpiredSignature", ": missing kid",
# ": unknown signing key"); the CLI surfaces Unauthenticated as
# "whoami requires authentication: ...". A JWKS-refresh failure comes back as
# Code::Internal "OIDC key refresh failed", which is deliberately NOT matched
# here because re-minting the caller's token would not fix a server-side JWKS
# problem.
AUTH_FAILURE_MARKERS = (
    "invalid token",
    "requires authentication",
    "unauthenticated",
    "expiredsignature",
)


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def gateway_dir(gateway_name: str) -> Path:
    return Path.home() / ".config" / "openshell" / "gateways" / gateway_name


def load_json(path: Path) -> dict:
    try:
        with path.open() as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def read_metadata(gw_dir: Path) -> dict:
    return load_json(gw_dir / "metadata.json")


def resolve_config(args: argparse.Namespace, meta: dict) -> tuple[str, str]:
    """Return (issuer, client_id), preferring flags, then env, then metadata."""
    issuer = (
        args.issuer
        or os.environ.get("OPENSHELL_OIDC_ISSUER")
        or meta.get("oidc_issuer")
    )
    client_id = (
        args.client_id
        or os.environ.get("OPENSHELL_OIDC_CLIENT_ID")
        or meta.get("oidc_client_id")
    )
    if not issuer:
        raise SystemExit(
            "error: OIDC issuer not found (pass --issuer, set "
            "OPENSHELL_OIDC_ISSUER, or ensure metadata.json has oidc_issuer)"
        )
    if not client_id:
        raise SystemExit(
            "error: OIDC client id not found (pass --client-id, set "
            "OPENSHELL_OIDC_CLIENT_ID, or ensure metadata.json has oidc_client_id)"
        )
    return issuer, client_id


def vault_addr(args: argparse.Namespace) -> str:
    addr = args.vault_addr or os.environ.get("VAULT_ADDR")
    if not addr:
        raise SystemExit(
            "error: VAULT_ADDR is not set (needed to read the client secret "
            "from Vault); export VAULT_ADDR or set "
            "OPENSHELL_OIDC_CLIENT_SECRET directly"
        )
    return addr.rstrip("/")


def read_vault_token() -> str | None:
    """Resolve the Vault token from env, then the CLI token helper file."""
    token = os.environ.get("VAULT_TOKEN")
    if token:
        return token.strip()
    helper = Path.home() / ".vault-token"
    try:
        token = helper.read_text().strip()
        return token or None
    except FileNotFoundError:
        return None


def vault_kv2_read(
    addr: str, token: str, mount: str, path: str, field: str
) -> tuple[str | None, int]:
    """Read a KV v2 field over the HTTP API.

    Returns (value, status). value is None on any non-200 (status carries the
    HTTP code so the caller can distinguish 403/404 auth problems from success).
    """
    url = f"{addr}/v1/{urllib.parse.quote(mount)}/data/{urllib.parse.quote(path)}"
    req = urllib.request.Request(url, headers={"X-Vault-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as exc:
        return None, exc.code
    except urllib.error.URLError as exc:
        raise SystemExit(f"error: could not reach Vault at {addr}: {exc.reason}")
    data = payload.get("data", {}).get("data", {})
    value = data.get(field)
    if not value:
        raise SystemExit(
            f"error: Vault secret {mount}/{path} has no field '{field}'"
        )
    return value, 200


def vault_oidc_login(addr: str, no_browser: bool) -> None:
    """Perform 'vault login -method=oidc', refreshing ~/.vault-token.

    The OIDC callback flow (localhost listener + PKCE exchange) is intricate and
    version-specific, so this delegates to the vault CLI, which implements it
    correctly. With no_browser=True it passes skip_browser=true so the CLI only
    prints the authentication URL for the user to open manually.
    """
    cmd = ["vault", "login", "-method=oidc", f"-address={addr}"]
    if no_browser:
        cmd.append("skip_browser=true")
    log(f"authenticating to Vault via OIDC: {' '.join(cmd)}")
    try:
        # Inherit stdio so the CLI can print the auth URL / open the browser and
        # the user sees the prompts directly.
        result = subprocess.run(cmd)
    except FileNotFoundError:
        raise SystemExit(
            "error: Vault token is missing/expired and the 'vault' CLI was not "
            "found on PATH to perform OIDC login"
        )
    if result.returncode != 0:
        raise SystemExit(
            f"error: 'vault login -method=oidc' failed (exit {result.returncode})"
        )


def get_client_secret(args: argparse.Namespace) -> str:
    secret = os.environ.get("OPENSHELL_OIDC_CLIENT_SECRET")
    if secret:
        return secret

    # Fall back to Vault over the HTTP API (KV v2). Only reached when the env
    # var above is unset.
    addr = vault_addr(args)
    token = read_vault_token()

    if token is not None:
        value, status = vault_kv2_read(
            addr, token, args.vault_mount, args.vault_path, args.vault_field
        )
        if status == 200 and value:
            return value
        if status not in (401, 403):
            raise SystemExit(
                f"error: Vault read returned HTTP {status} for "
                f"{args.vault_mount}/{args.vault_path}"
            )
        log(f"Vault token rejected (HTTP {status}); re-authenticating via OIDC")

    if args.no_vault_login:
        raise SystemExit(
            "error: no valid Vault token and --no-vault-login was set; run "
            "'vault login -method=oidc' first or set "
            "OPENSHELL_OIDC_CLIENT_SECRET"
        )

    vault_oidc_login(addr, no_browser=args.no_browser)

    token = read_vault_token()
    if not token:
        raise SystemExit(
            "error: Vault OIDC login did not produce a token at ~/.vault-token"
        )
    value, status = vault_kv2_read(
        addr, token, args.vault_mount, args.vault_path, args.vault_field
    )
    if status != 200 or not value:
        raise SystemExit(
            f"error: Vault read failed after OIDC login (HTTP {status})"
        )
    return value


def token_endpoint(issuer: str) -> str:
    return issuer.rstrip("/") + "/protocol/openid-connect/token"


def mint_access_token(issuer: str, client_id: str, client_secret: str) -> str:
    body = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        token_endpoint(issuer),
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace").strip()
        raise SystemExit(
            f"error: token endpoint returned {exc.code}: {detail or exc.reason}"
        )
    except urllib.error.URLError as exc:
        raise SystemExit(f"error: could not reach token endpoint: {exc.reason}")
    token = payload.get("access_token")
    if not token:
        raise SystemExit("error: token response contained no access_token")
    return token


def decode_exp(access_token: str) -> int | None:
    """Return the JWT 'exp' (epoch seconds) without verifying the signature."""
    try:
        payload_b64 = access_token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload_b64))
        exp = claims.get("exp")
        return int(exp) if exp is not None else None
    except Exception:
        return None


def seconds_remaining(token_path: Path) -> int | None:
    data = load_json(token_path)
    token = data.get("access_token")
    if not token:
        return None
    exp = decode_exp(token)
    if exp is None:
        return None
    return int(exp - time.time())


def write_token_file(token_path: Path, updates: dict) -> None:
    """Merge updates into the existing token JSON and write atomically."""
    data = load_json(token_path)
    data.update(updates)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(token_path.parent))
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, token_path)  # atomic; no partial token ever seen
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def refresh(args: argparse.Namespace) -> None:
    gw_dir = gateway_dir(args.gateway)
    token_path = gw_dir / "oidc_token.json"
    meta = read_metadata(gw_dir)
    issuer, client_id = resolve_config(args, meta)

    if args.if_expiring is not None:
        remaining = seconds_remaining(token_path)
        if remaining is not None and remaining > args.if_expiring:
            log(f"token still valid for {remaining}s; skipping mint")
            return

    secret = get_client_secret(args)
    token = mint_access_token(issuer, client_id, secret)
    write_token_file(
        token_path,
        {"access_token": token, "issuer": issuer, "client_id": client_id},
    )
    remaining = seconds_remaining(token_path)
    suffix = f" (valid ~{remaining}s)" if remaining is not None else ""
    log(f"refreshed {token_path}{suffix}")


def run_openshell(gateway: str, command: list[str]) -> subprocess.CompletedProcess:
    full = ["openshell", "-g", gateway, *command]
    return subprocess.run(full, capture_output=True, text=True)


def looks_like_auth_failure(proc: subprocess.CompletedProcess) -> bool:
    blob = (proc.stdout + proc.stderr).lower()
    return any(marker in blob for marker in AUTH_FAILURE_MARKERS)


def exec_with_retry(args: argparse.Namespace) -> int:
    # Ensure we have a token before the first attempt.
    refresh(args)

    proc = run_openshell(args.gateway, args.command)
    if proc.returncode == 0:
        sys.stdout.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        return 0

    if looks_like_auth_failure(proc):
        log("openshell reported an auth failure; re-minting token and retrying")
        # Force a mint regardless of remaining lifetime.
        forced = argparse.Namespace(**vars(args))
        forced.if_expiring = None
        refresh(forced)
        proc = run_openshell(args.gateway, args.command)

    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    return proc.returncode


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Refresh an OpenShell gateway's oidc_token.json in place.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "-g",
        "--gateway",
        default=os.environ.get("OPENSHELL_GATEWAY_NAME"),
        help="Gateway name (dir under ~/.config/openshell/gateways/). "
        "Defaults to $OPENSHELL_GATEWAY_NAME.",
    )
    p.add_argument("--issuer", help="OIDC issuer URL override.")
    p.add_argument("--client-id", help="OIDC client id override.")
    p.add_argument(
        "--if-expiring",
        type=int,
        metavar="SECONDS",
        help="Only re-mint if fewer than SECONDS of token life remain.",
    )
    p.add_argument(
        "--vault-addr",
        help="Vault address (defaults to $VAULT_ADDR). Only used for the "
        "client-secret fallback when OPENSHELL_OIDC_CLIENT_SECRET is unset.",
    )
    p.add_argument(
        "--vault-mount", default=DEFAULT_VAULT_MOUNT, help="Vault KV v2 mount."
    )
    p.add_argument(
        "--vault-path", default=DEFAULT_VAULT_PATH, help="Vault KV v2 path."
    )
    p.add_argument(
        "--vault-field", default=DEFAULT_VAULT_FIELD, help="Vault field name."
    )
    p.add_argument(
        "--no-browser",
        action="store_true",
        help="On Vault OIDC login, do not open a browser; print the auth URL "
        "instead (passes skip_browser=true to 'vault login').",
    )
    p.add_argument(
        "--no-vault-login",
        action="store_true",
        help="Do not attempt 'vault login -method=oidc' if the Vault token is "
        "missing/expired; fail instead.",
    )
    p.add_argument(
        "--exec",
        dest="do_exec",
        action="store_true",
        help="After refreshing, run: openshell -g <gateway> <command...>, "
        "re-minting once on auth failure. Command follows '--'.",
    )
    p.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="With --exec, the openshell subcommand after '--'.",
    )
    args = p.parse_args(argv)

    if not args.gateway:
        p.error("gateway name required (pass -g or set OPENSHELL_GATEWAY_NAME)")
    if args.command and args.command[0] == "--":
        args.command = args.command[1:]
    if args.do_exec and not args.command:
        p.error("--exec requires a command after '--'")
    return args


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if args.do_exec:
        return exec_with_retry(args)
    refresh(args)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
