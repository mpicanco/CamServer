"""Unit tests for the crypto module."""

from __future__ import annotations

import pytest
from cryptography.fernet import InvalidToken

from camserver.crypto import SecretBox, SecretDecryptError, safe_decrypt


def test_encrypt_decrypt_roundtrip(tmp_path):
    box = SecretBox(tmp_path / "secret.key")
    token = box.encrypt("s3cr3t-password")
    assert token != "s3cr3t-password"
    assert box.decrypt(token) == "s3cr3t-password"


def test_key_file_permissions(tmp_path):
    key_path = tmp_path / "secret.key"
    SecretBox(key_path)
    assert key_path.stat().st_mode & 0o777 == 0o600


def test_key_is_reused(tmp_path):
    key_path = tmp_path / "secret.key"
    box1 = SecretBox(key_path)
    token = box1.encrypt("pw")
    box2 = SecretBox(key_path)
    assert box2.decrypt(token) == "pw"


def test_decrypt_with_wrong_key_fails(tmp_path):
    box1 = SecretBox(tmp_path / "a.key")
    token = box1.encrypt("pw")
    box2 = SecretBox(tmp_path / "b.key")
    with pytest.raises(InvalidToken):
        box2.decrypt(token)


def test_safe_decrypt_none_returns_none(tmp_path):
    box = SecretBox(tmp_path / "secret.key")
    assert safe_decrypt(box, None) is None
    assert safe_decrypt(box, "") is None


def test_safe_decrypt_raises_on_corrupt(tmp_path):
    box = SecretBox(tmp_path / "secret.key")
    with pytest.raises(SecretDecryptError):
        safe_decrypt(box, "not-a-valid-token")
