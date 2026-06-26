"""Хеширование и проверка паролей (argon2id).

Пароли никогда не хранятся и не логируются в открытом виде - только хеш.
Алгоритм по умолчанию argon2id; passlib хранит параметры внутри строки хеша,
поэтому при их смене старые хеши остаются проверяемыми (needs_rehash подскажет
момент пересчёта).
"""
from __future__ import annotations

from passlib.context import CryptContext

_pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")


def hash_password(password: str) -> str:
    """Вернуть argon2-хеш пароля."""
    return _pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Проверить пароль против хеша. Сравнение постоянного времени (внутри passlib)."""
    return _pwd_context.verify(password, password_hash)


def needs_rehash(password_hash: str) -> bool:
    """True, если хеш создан старыми параметрами и его стоит пересчитать при входе."""
    return _pwd_context.needs_update(password_hash)
