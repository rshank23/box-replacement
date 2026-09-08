from __future__ import annotations

import hashlib
from pathlib import Path

_CHUNK = 1024 * 1024


def sha256_file(path: str | Path) -> str:
    """Streamed SHA-256 of a file (constant memory, safe for 500 MB documents)."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def md5_file(path: str | Path) -> str:
    """Streamed MD5, used only to compare against Vault's ``md5checksum__v``."""
    digest = hashlib.md5()  # noqa: S324 - integrity check against Vault, not a security control
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def external_id(source_path: str, checksum: str) -> str:
    """Deterministic idempotency key for Vault document creation."""
    return hashlib.sha256(f"{source_path}|{checksum}".encode()).hexdigest()
