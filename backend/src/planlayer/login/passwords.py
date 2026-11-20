"""Local accounts' passwords (ADR 0030): scrypt from the standard library, salted per password.

Stored as ``scrypt$n$r$p$salt$digest`` (hex), so the cost can be raised later without breaking
the hashes already stored.
"""

import hashlib
import hmac
import secrets
from functools import cache

MIN_LENGTH = 12
_N, _R, _P = 2**15, 8, 1
# scrypt needs 128 * n * r bytes; OpenSSL's default ceiling (32 MiB) is just below n = 2**15.
_MAXMEM = 64 * 1024 * 1024


class PasswordError(ValueError):
    """A password that can't be used as one."""


def check_new(password: str) -> None:
    if len(password) < MIN_LENGTH:
        raise PasswordError(f"a password needs at least {MIN_LENGTH} characters")
    if not password.strip():
        raise PasswordError("a password can't be only spaces")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, maxmem=_MAXMEM)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${digest.hex()}"


def verify(password: str, stored: str) -> bool:
    kind, n, r, p, salt, digest = stored.split("$")
    if kind != "scrypt":
        raise ValueError(f"unknown password hash {kind!r}")
    candidate = hashlib.scrypt(
        password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), maxmem=_MAXMEM
    )
    return hmac.compare_digest(candidate, bytes.fromhex(digest))


@cache
def _dummy_hash() -> str:
    return hash_password(secrets.token_urlsafe(16))


def verify_nothing(password: str) -> None:
    """Spend a verification's time when a login names no local account, so the answer takes as
    long either way and doesn't tell which logins exist."""
    verify(password, _dummy_hash())
