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
import ipaddress
import re
import secrets
import threading
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
    # compare_digest raises on non-ASCII text, and a cookie is whatever the
    # client sent: "\351" in one used to 500 the page, traceback and all.
    if not re.fullmatch(r"[0-9a-f]{64}", signature):
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
    """Ours, found by hand. SimpleCookie drops the whole header when any other
    cookie on the host is non-standard (a space, a JSON value), which made a
    valid session look logged out."""
    for part in (header or "").split(";"):
        name, _, value = part.strip().partition("=")
        if name == COOKIE and value:
            return value.strip('"')
    return None


def client_key(address):
    """Who a login attempt counts against. IPv6 by /64, since one host is
    handed the whole block and could otherwise start afresh at every address."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return address
    return str(ipaddress.ip_network(f"{ip}/64", strict=False)) if ip.version == 6 else str(ip)


class Throttle:
    """Crude per-process backoff. Enough to make guessing pointless without
    adding a dependency or a table to maintain.

    An attempt takes its slot before the password is checked, under a lock:
    checked after, thirty guesses sent at once all got through a limit of six
    while scrypt ran."""

    def __init__(self, limit=6, window=900):
        self.limit = limit
        self.window = window
        self.hits = {}
        self.lock = threading.Lock()

    def admit(self, key):
        """Count an attempt and say whether it may go ahead."""
        now = time.time()
        with self.lock:
            tries = [t for t in self.hits.get(key, []) if now - t < self.window]
            if len(tries) >= self.limit:
                self.hits[key] = tries
                return False
            self.hits[key] = tries + [now]
            return True

    def clear(self, key):
        with self.lock:
            self.hits.pop(key, None)


def random_password():
    """Four groups of four from 31 unambiguous characters: about 79 bits. The
    old four words out of twelve was 20,736 possibilities."""
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    return "-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(4))


def ensure_secret(cfg, save):
    """A signing secret, generated once and persisted."""
    secret = (cfg.get("auth") or {}).get("session_secret")
    if not secret:
        secret = secrets.token_urlsafe(32)
        cfg.setdefault("auth", {})["session_secret"] = secret
        save(cfg)
    return secret
