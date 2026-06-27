"""Шифрование чувствительных полей вендоров (Fernet, симметричное).

Ключ хранится в VENDOR_CRYPTO_KEY (.env). Зашифрованные значения
сохраняются в БД как строка (base64-токен Fernet).

Fernet гарантирует: шифрование + аутентификацию (HMAC). Повреждённый
или подменённый токен вызывает InvalidToken, не расшифровывается молча.
"""
from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken


def _fernet() -> Fernet:
    key = os.environ.get("VENDOR_CRYPTO_KEY", "")
    if not key:
        raise RuntimeError(
            "VENDOR_CRYPTO_KEY не задан в .env — "
            "сгенерируйте ключ: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    return Fernet(key.encode())


def encrypt(plaintext: str) -> str:
    """Зашифровать строку. Возвращает base64-токен Fernet."""
    if not plaintext:
        return ""
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    """Расшифровать токен. Возвращает исходную строку.

    Raises InvalidToken если токен повреждён или ключ не совпадает.
    """
    if not token:
        return ""
    return _fernet().decrypt(token.encode()).decode()


__all__ = ["encrypt", "decrypt", "InvalidToken"]
