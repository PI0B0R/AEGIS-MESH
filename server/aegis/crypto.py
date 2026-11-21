"""Encryption at rest for sensitive columns (task point 16).

Each value is sealed with AES-256-GCM. The column name is bound as associated data,
so a ciphertext copied into another column does not decrypt. The key lives outside
the database: AEGIS_KEY (base64) or a key file created with 0600 permissions.
"""
import base64
import os
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PREFIX = "enc1:"


class FieldCipher:
    def __init__(self, key: bytes):
        if len(key) != 32:
            raise ValueError("Klucz szyfrowania musi mieć 32 bajty.")
        self._aead = AESGCM(key)

    @classmethod
    def load(cls, key_path: str | os.PathLike) -> "FieldCipher":
        env = os.environ.get("AEGIS_KEY")
        if env:
            return cls(base64.b64decode(env))
        path = Path(key_path)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            key = AESGCM.generate_key(bit_length=256)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(base64.b64encode(key))
        return cls(base64.b64decode(path.read_bytes().strip()))

    def seal(self, value: str | None, column: str) -> str | None:
        if value is None or value == "":
            return value
        nonce = os.urandom(12)
        sealed = self._aead.encrypt(nonce, value.encode("utf-8"), column.encode("ascii"))
        return PREFIX + base64.b64encode(nonce + sealed).decode("ascii")

    def open(self, token: str | None, column: str) -> str | None:
        if not token or not token.startswith(PREFIX):
            return token
        raw = base64.b64decode(token[len(PREFIX):])
        return self._aead.decrypt(raw[:12], raw[12:], column.encode("ascii")).decode("utf-8")
