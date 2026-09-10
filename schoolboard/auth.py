"""Session auth for the public entrance.

The board is reached three ways, and they do not deserve the same treatment:

  * localhost — the mini's own screen
  * the tailnet — devices Tailscale has already authenticated
  * the Cloudflare tunnel — the open internet

Tailscale is already an authenticator, so demanding a password there is friction
without benefit. The tunnel is not, so it gets its own listener (a separate port,
bound to localhost, which cloudflared alone talks to) where a session is
required. Separate ports rather than sniffing headers: a forwarded-for header is
a claim, a listening socket is a fact.

stdlib only — scrypt for the password, HMAC for the cookie.
"""
import base64
import hashlib
import hmac
import http.cookies
import os
import secrets
import time

COOKIE = "sb_session"
SCRYPT = dict(n=2**14, r=8, p=1, dklen=32)


def hash_password(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, **SCRYPT)
    return f"scrypt${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password, stored):
    try:
        scheme, salt_b64, digest_b64 = (stored or "").split("$")
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
    except (ValueError, TypeError):
        return False
    actual = hashlib.scrypt(password.encode("utf-8"), salt=salt, **SCRYPT)
    return hmac.compare_digest(actual, expected)


def _sign(payload, secret):
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def issue(secret, days=30):
    expires = int(time.time()) + days * 86400
    payload = f"{expires}.{secrets.token_hex(8)}"
    return f"{payload}.{_sign(payload, secret)}"


def valid(token, secret):
    try:
        expires, nonce, signature = (token or "").split(".")
    except ValueError:
        return False
    payload = f"{expires}.{nonce}"
    if not hmac.compare_digest(_sign(payload, secret), signature):
        return False
    try:
        return int(expires) > time.time()
    except ValueError:
        return False


def cookie_header(token, days=30, secure=True):
    parts = [f"{COOKIE}={token}", "Path=/", "HttpOnly", "SameSite=Lax",
             f"Max-Age={days * 86400}"]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def clear_header():
    return f"{COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"


def read_cookie(header):
    if not header:
        return None
    jar = http.cookies.SimpleCookie()
    try:
        jar.load(header)
    except http.cookies.CookieError:
        return None
    morsel = jar.get(COOKIE)
    return morsel.value if morsel else None


class Throttle:
    """Crude per-process backoff. Enough to make guessing pointless without
    adding a dependency or a table to maintain."""

    def __init__(self, limit=6, window=900):
        self.limit = limit
        self.window = window
        self.hits = {}

    def blocked(self, key):
        now = time.time()
        tries = [t for t in self.hits.get(key, []) if now - t < self.window]
        self.hits[key] = tries
        return len(tries) >= self.limit

    def record(self, key):
        self.hits.setdefault(key, []).append(time.time())

    def clear(self, key):
        self.hits.pop(key, None)


def ensure_secret(cfg, save):
    """A signing secret, generated once and persisted."""
    secret = (cfg.get("auth") or {}).get("session_secret")
    if not secret:
        secret = secrets.token_urlsafe(32)
        cfg.setdefault("auth", {})["session_secret"] = secret
        save(cfg)
    return secret
