"""Dhan access-token manager.

Mints a 24h access token headlessly via Dhan's TOTP flow
(POST https://auth.dhan.co/app/generateAccessToken with dhanClientId+PIN+TOTP),
with the API-key consent flow as a documented fallback (needs a browser step).

Security: secrets are read from env files only, never logged or printed.
The minted token is written to the project .env (chmod 600, gitignored).
"""
from __future__ import annotations

import json
import os
import stat
from datetime import datetime
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PROJECT_ENV = PROJECT_ROOT / ".env"

# Cred sources, in precedence order (later files do not override earlier keys)
ENV_SOURCES = [
    PROJECT_ROOT / ".dhan-creds.env",            # DHAN_API_KEY / DHAN_API_SECRET
    Path("/etc/claude-soma/algo-trader-dhan.env"),
    PROJECT_ENV,                                  # minted token lands here
    Path("/home/ubuntu/finAgent/.env"),           # client_id, PIN, TOTP secret
]

AUTH_BASE = "https://auth.dhan.co"
API_BASE = "https://api.dhan.co/v2"


def _load_env() -> dict[str, str]:
    merged: dict[str, str] = {}
    for path in ENV_SOURCES:
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip()
            if v and k not in merged:
                merged[k] = v
    return merged


def _mask(s: str) -> str:
    return f"<{len(s)} chars>" if s else "<empty>"


def mint_token_totp(env: dict[str, str]) -> tuple[str | None, str]:
    """Headless mint via client_id + PIN + TOTP. Returns (token|None, diagnostic)."""
    import pyotp

    client_id = env.get("DHAN_CLIENT_ID", "")
    pin = env.get("DHAN_PIN", "")
    totp_secret = env.get("DHAN_TOTP_SECRET", "")
    if not (client_id and pin and totp_secret):
        return None, "missing client_id/pin/totp_secret"

    totp_now = pyotp.TOTP(totp_secret).now()
    attempts = [
        {},  # documented: no special headers
        {  # fallback variant: some deployments validate app credentials too
            "app_id": env.get("DHAN_API_KEY", ""),
            "app_secret": env.get("DHAN_API_SECRET", ""),
        },
    ]
    diags = []
    for headers in attempts:
        try:
            r = requests.post(
                f"{AUTH_BASE}/app/generateAccessToken",
                params={"dhanClientId": client_id, "pin": pin, "totp": totp_now},
                headers={k: v for k, v in headers.items() if v},
                timeout=20,
            )
        except requests.RequestException as e:
            diags.append(f"request error: {type(e).__name__}")
            continue
        body: dict = {}
        try:
            body = r.json()
        except ValueError:
            pass
        token = body.get("accessToken") or body.get("access_token")
        if r.status_code == 200 and token:
            return token, f"ok (expiry={body.get('expiryTime', '?')})"
        # Never echo body fields that could contain secrets; codes/messages only
        diags.append(
            f"HTTP {r.status_code} hdrs={'app' if headers else 'none'} "
            f"err={body.get('errorCode', body.get('status', '?'))} "
            f"msg={str(body.get('errorMessage', body.get('message', '')))[:120]}"
        )
    return None, " | ".join(diags)


def save_token(token: str, client_id: str) -> None:
    lines = []
    if PROJECT_ENV.exists():
        lines = [
            l for l in PROJECT_ENV.read_text().splitlines()
            if not l.startswith(("DHAN_ACCESS_TOKEN=", "DHAN_CLIENT_ID=", "DHAN_TOKEN_MINTED_AT="))
        ]
    lines += [
        f"DHAN_ACCESS_TOKEN={token}",
        f"DHAN_CLIENT_ID={client_id}",
        f"DHAN_TOKEN_MINTED_AT={datetime.now().isoformat(timespec='seconds')}",
    ]
    PROJECT_ENV.write_text("\n".join(lines) + "\n")
    PROJECT_ENV.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 600


def probe_profile(token: str) -> dict:
    """Read-only auth probe. Returns profile dict with client id removed."""
    r = requests.get(f"{API_BASE}/profile", headers={"access-token": token}, timeout=20)
    try:
        d = r.json()
    except ValueError:
        d = {"raw_status": r.status_code}
    d.pop("dhanClientId", None)
    d["_http"] = r.status_code
    return d


def get_valid_token(verbose: bool = False) -> str | None:
    """Return a working access token: reuse saved one if valid, else mint."""
    env = _load_env()
    saved = env.get("DHAN_ACCESS_TOKEN", "")
    if saved:
        prof = probe_profile(saved)
        if prof.get("_http") == 200:
            if verbose:
                print("reusing saved token; profile:", json.dumps(prof))
            return saved
    token, diag = mint_token_totp(env)
    if verbose:
        print(f"mint via TOTP: {diag}; token={_mask(token or '')}")
    if token:
        save_token(token, env.get("DHAN_CLIENT_ID", ""))
        return token
    return None


if __name__ == "__main__":
    tok = get_valid_token(verbose=True)
    if tok:
        print("PROFILE:", json.dumps(probe_profile(tok), indent=1))
        print("AUTH OK")
    else:
        print("AUTH FAILED — see diagnostics above")
        raise SystemExit(1)
