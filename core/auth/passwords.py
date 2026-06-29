"""Хеширование и проверка паролей (argon2id).

Пароли никогда не хранятся и не логируются в открытом виде - только хеш.
Алгоритм по умолчанию argon2id; passlib хранит параметры внутри строки хеша,
поэтому при их смене старые хеши остаются проверяемыми (needs_rehash подскажет
момент пересчёта).
"""
from __future__ import annotations

import re

from passlib.context import CryptContext

_pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

# Требования к постоянному паролю (при первой смене). Единый источник истины:
# клиент дублирует их для подсказки, сервер - проверяет (доверять можно только серверу).
PASSWORD_MIN_LEN = 8


def password_policy_errors(password: str) -> list[str]:
    """Вернуть список нарушений политики пароля. Пустой список = пароль валиден."""
    errors: list[str] = []
    if len(password) < PASSWORD_MIN_LEN:
        errors.append(f"Длина пароля должна быть не менее {PASSWORD_MIN_LEN} символов")
    if not re.search(r"[A-ZА-ЯЁ]", password):
        errors.append("Минимум одна заглавная буква")
    if not re.search(r"[a-zа-яё]", password):
        errors.append("Минимум одна строчная буква")
    if not re.search(r"[^A-Za-zА-Яа-яЁё0-9]", password):
        errors.append("Минимум один специальный символ")
    return errors


def hash_password(password: str) -> str:
    """Вернуть argon2-хеш пароля."""
    return _pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Проверить пароль против хеша. Сравнение постоянного времени (внутри passlib)."""
    return _pwd_context.verify(password, password_hash)


def needs_rehash(password_hash: str) -> bool:
    """True, если хеш создан старыми параметрами и его стоит пересчитать при входе."""
    return _pwd_context.needs_update(password_hash)
