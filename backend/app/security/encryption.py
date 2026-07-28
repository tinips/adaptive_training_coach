"""Encryption-at-rest helpers for provider credentials."""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken
from pydantic import SecretStr


class EncryptionError(ValueError):
    """Raised when token encryption is unavailable or ciphertext is invalid."""


class TokenCipher:
    """Encrypt and decrypt OAuth tokens with an application Fernet key."""

    def __init__(self, key: SecretStr | str | bytes) -> None:
        try:
            if isinstance(key, SecretStr):
                raw_key = key.get_secret_value().encode("ascii")
            elif isinstance(key, str):
                raw_key = key.encode("ascii")
            else:
                raw_key = key
            self._fernet = Fernet(raw_key)
        except (TypeError, UnicodeError, ValueError) as exc:
            raise EncryptionError("The application encryption key is invalid.") from exc

    @staticmethod
    def generate_key() -> str:
        """Generate a URL-safe key suitable for ``APP_ENCRYPTION_KEY``."""

        return Fernet.generate_key().decode("ascii")

    def encrypt(self, plaintext: str) -> str:
        """Return authenticated ciphertext for a non-empty token."""

        if not plaintext:
            raise EncryptionError("An empty OAuth token cannot be encrypted.")
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        """Decrypt a token without including token material in errors."""

        if not ciphertext:
            raise EncryptionError("OAuth token ciphertext is missing.")
        try:
            return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeError, ValueError) as exc:
            raise EncryptionError("OAuth token ciphertext is invalid.") from exc
