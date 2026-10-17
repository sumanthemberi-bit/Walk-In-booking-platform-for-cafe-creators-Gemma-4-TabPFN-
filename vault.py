"""Keeps each user's email app password encrypted in the database.

Key: the platform's .secret_key file (created on first run, never leaves this machine).
Cipher: SHA-256 counter-mode keystream + HMAC-SHA256 tag, using only the standard library.
This protects passwords if the database file is copied without the key file.
"""
import base64
import hashlib
import hmac
import secrets
from pathlib import Path

KEY_FILE = Path(__file__).parent / ".secret_key"


def key() -> str:
    if not KEY_FILE.exists():
        KEY_FILE.write_text(secrets.token_hex(32))
    return KEY_FILE.read_text().strip()


def _k(purpose: bytes) -> bytes:
    return hmac.new(bytes.fromhex(key()), purpose, hashlib.sha256).digest()


def _stream(nonce: bytes, n: int) -> bytes:
    k, out, i = _k(b"enc"), b"", 0
    while len(out) < n:
        out += hashlib.sha256(k + nonce + i.to_bytes(8, "big")).digest()
        i += 1
    return out[:n]


def seal(text: str) -> str:
    if not text:
        return ""
    data, nonce = text.encode(), secrets.token_bytes(16)
    ct = bytes(a ^ b for a, b in zip(data, _stream(nonce, len(data))))
    tag = hmac.new(_k(b"mac"), nonce + ct, hashlib.sha256).digest()[:16]
    return base64.urlsafe_b64encode(nonce + tag + ct).decode()


def unseal(token: str) -> str:
    if not token:
        return ""
    raw = base64.urlsafe_b64decode(token.encode())
    nonce, tag, ct = raw[:16], raw[16:32], raw[32:]
    if not hmac.compare_digest(tag, hmac.new(_k(b"mac"), nonce + ct, hashlib.sha256).digest()[:16]):
        raise ValueError("Stored password can't be decrypted (key changed?). Reconnect your email.")
    return bytes(a ^ b for a, b in zip(ct, _stream(nonce, len(ct)))).decode()
