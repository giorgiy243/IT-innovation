"""CLI: создать арендатора и/или пользователя платформы.

Пароль НИКОГДА не передаётся аргументом командной строки (виден в истории
и в списке процессов). Источник пароля по приоритету:
  1) --password-stdin  - читаем первую строку stdin (для автоматизации);
  2) переменная окружения ITINV_PASSWORD;
  3) интерактивный ввод getpass с подтверждением (по умолчанию).

Примеры:
  python scripts/create_user.py --login ivanov
  python scripts/create_user.py --login ivanov --tenant "АйТек"
  echo "s3cret" | python scripts/create_user.py --login ivanov --password-stdin
  python scripts/create_user.py --login ivanov --reset-password   # сменить пароль
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

# Windows-консоль по умолчанию cp1252 и падает на кириллице в выводе.
# Переключаем потоки на UTF-8 (Python 3.7+); если нельзя - не критично.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

# Корень репозитория в sys.path, чтобы импортировать пакет core при прямом запуске.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from core.auth.passwords import hash_password  # noqa: E402
from core.db import session_scope  # noqa: E402
from core.models import Tenant, User  # noqa: E402

MIN_PASSWORD_LEN = 8
DEFAULT_TENANT = "АйТек"


def read_password(from_stdin: bool) -> str:
    """Получить пароль из stdin / env / интерактивного ввода. Валидирует длину."""
    if from_stdin:
        password = sys.stdin.readline().rstrip("\n")
    elif os.environ.get("ITINV_PASSWORD"):
        password = os.environ["ITINV_PASSWORD"]
    else:
        password = getpass.getpass("Пароль: ")
        if password != getpass.getpass("Повтор пароля: "):
            sys.exit("Ошибка: пароли не совпадают.")

    if len(password) < MIN_PASSWORD_LEN:
        sys.exit(f"Ошибка: пароль короче {MIN_PASSWORD_LEN} символов.")
    return password


def get_or_create_tenant(db, name: str) -> Tenant:
    """Найти арендатора по имени или создать нового."""
    tenant = db.execute(
        select(Tenant).where(Tenant.name == name)
    ).scalar_one_or_none()
    if tenant is None:
        tenant = Tenant(name=name)
        db.add(tenant)
        db.flush()
        print(f"Создан арендатор: {name} (id={tenant.id})")
    return tenant


def main() -> None:
    parser = argparse.ArgumentParser(description="Создать арендатора/пользователя.")
    parser.add_argument("--login", required=True, help="Логин пользователя.")
    parser.add_argument(
        "--tenant", default=DEFAULT_TENANT, help=f"Имя арендатора (по умолчанию {DEFAULT_TENANT})."
    )
    parser.add_argument(
        "--password-stdin", action="store_true", help="Читать пароль из stdin."
    )
    parser.add_argument(
        "--reset-password",
        action="store_true",
        help="Если пользователь существует - сменить ему пароль.",
    )
    args = parser.parse_args()

    login = args.login.strip()
    if not login:
        sys.exit("Ошибка: пустой логин.")

    with session_scope() as db:
        tenant = get_or_create_tenant(db, args.tenant)

        existing = db.execute(
            select(User).where(User.tenant_id == tenant.id, User.login == login)
        ).scalar_one_or_none()

        if existing is not None and not args.reset_password:
            sys.exit(
                f"Ошибка: пользователь '{login}' уже есть в '{args.tenant}'. "
                f"Используй --reset-password для смены пароля."
            )

        password = read_password(args.password_stdin)

        if existing is not None:
            existing.password_hash = hash_password(password)
            existing.is_active = True
            print(f"Пароль пользователя '{login}' обновлён.")
        else:
            db.add(
                User(
                    tenant_id=tenant.id,
                    login=login,
                    password_hash=hash_password(password),
                )
            )
            print(f"Создан пользователь '{login}' в арендаторе '{args.tenant}'.")


if __name__ == "__main__":
    main()
