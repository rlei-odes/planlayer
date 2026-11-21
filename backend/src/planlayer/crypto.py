"""Pay and the OpenID Connect client secret, encrypted at rest (ADR 0029).

Values are sealed with AES-256-GCM in Python, so the database never sees a key or a plain
salary. A sealed value is ``version (1 byte) ‖ key id (4 bytes) ‖ nonce (12 bytes) ‖ ciphertext
and tag``; the key id is the first 4 bytes of the key's SHA-256. Each value gets a fresh random
nonce, so equal salaries look different. The associated data names the column a value belongs
to, so a value copied into another column doesn't open.

The columns use the types below: SQLAlchemy seals what a statement writes and opens what it
reads. Blank stays NULL and needs no key; zero is sealed like any amount.

The key comes from ``PLANLAYER_PAY_KEY`` (32 bytes, base64), set by ``configure`` when an engine
is created. It is process-wide: the column types have no other way to reach it.
"""

import base64
import binascii
import hashlib
import os
from decimal import Decimal
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import SecretStr
from sqlalchemy import LargeBinary
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator

KEY_ENV = "PLANLAYER_PAY_KEY"
VERSION = 1
_HEADER = 1 + 4 + 12


class PayKeyError(Exception):
    """A sealed value can't be read or written: the key is missing or another one."""


_key: bytes | None = None


def parse_key(value: str) -> bytes:
    try:
        key = base64.b64decode(value.strip(), validate=True)
    except (binascii.Error, ValueError):
        raise ValueError(
            f"{KEY_ENV} is not base64 (make one with: planlayer new-pay-key)"
        ) from None
    if len(key) != 32:
        raise ValueError(f"{KEY_ENV} must be 32 bytes, base64 (planlayer new-pay-key)")
    return key


def configure(key: SecretStr | None) -> None:
    """The installation's key, or none."""
    global _key
    _key = parse_key(key.get_secret_value()) if key is not None else None


def new_key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


def key_id(key: bytes) -> bytes:
    return hashlib.sha256(key).digest()[:4]


def _current() -> bytes:
    if _key is None:
        raise PayKeyError(f"{KEY_ENV} is not set: pay and the login's client secret can't be read")
    return _key


def seal(plain: str, place: str) -> bytes:
    key = _current()
    nonce = os.urandom(12)
    sealed = AESGCM(key).encrypt(nonce, plain.encode(), place.encode())
    return bytes([VERSION]) + key_id(key) + nonce + sealed


def unseal(data: bytes, place: str) -> str:
    key = _current()
    if len(data) <= _HEADER or data[0] != VERSION:
        raise PayKeyError(f"{place}: not a sealed value")
    if data[1:5] != key_id(key):
        raise PayKeyError(f"{place} was sealed with another key than {KEY_ENV}")
    try:
        return AESGCM(key).decrypt(data[5:17], data[17:], place.encode()).decode()
    except InvalidTag:
        raise PayKeyError(f"{place}: a sealed value doesn't open (changed, or moved)") from None


class _Sealed(TypeDecorator[Any]):
    """A column holding sealed values; ``place`` (table.column) is the associated data."""

    impl = LargeBinary
    cache_ok = True

    def __init__(self, place: str) -> None:
        super().__init__()
        self.place = place

    def _plain(self, value: Any) -> str:
        raise NotImplementedError

    def _value(self, text: str) -> Any:
        raise NotImplementedError

    def process_bind_param(self, value: Any, dialect: Dialect) -> bytes | None:
        if value is None:
            return None
        if isinstance(value, bytes | memoryview):  # already sealed, e.g. written back unchanged
            return bytes(value)
        return seal(self._plain(value), self.place)

    def process_result_value(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        return self._value(unseal(bytes(value), self.place))


class SealedAmount(_Sealed):
    """An amount (``Decimal``, or an exact ``int``), sealed as its decimal text (T-12)."""

    cache_ok = True

    def _plain(self, value: Any) -> str:
        if isinstance(value, bool) or not isinstance(value, Decimal | int):
            raise TypeError(f"{self.place}: an amount is a Decimal, not {type(value).__name__}")
        return str(value)

    def _value(self, text: str) -> Decimal:
        return Decimal(text)


class SealedText(_Sealed):
    """A text, e.g. a client secret."""

    cache_ok = True

    def _plain(self, value: Any) -> str:
        return str(value)

    def _value(self, text: str) -> str:
        return text
