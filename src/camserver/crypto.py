"""Encryption of secrets (camera passwords) at rest using Fernet."""

from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class SecretBox:
    """Encrypt/decrypt short secrets with a key stored in a 0600 file."""

    def __init__(self, key_path: Path) -> None:
        self._key_path = Path(key_path)
        self._fernet = Fernet(self._load_or_create_key())

    def _load_or_create_key(self) -> bytes:
        self._key_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._key_path.parent.chmod(0o700)
        except OSError:
            pass

        if self._key_path.exists():
            key = self._key_path.read_bytes().strip()
            if key:
                return key

        key = Fernet.generate_key()
        fd = os.open(str(self._key_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(key)
        finally:
            # Ensure restrictive permissions even if umask is loose.
            try:
                self._key_path.chmod(0o600)
            except OSError:
                pass
        return key

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str) -> str:
        return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")


class SecretDecryptError(ValueError):
    """Raised when a stored secret cannot be decrypted (bad key / corrupt data)."""


def safe_decrypt(box: SecretBox, token: str | None) -> str | None:
    if not token:
        return None
    try:
        return box.decrypt(token)
    except (InvalidToken, ValueError):
        raise SecretDecryptError("unable to decrypt stored secret") from None
