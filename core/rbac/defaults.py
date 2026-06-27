"""Стартовый каталог RBAC: модули и роли «из коробки».

Данные, а не код: это лишь начальное наполнение таблиц (modules/roles/role_modules)
через scripts/seed_rbac.py. После сидинга админ меняет права в БД без релиза
(включает модули, раздаёт роли). Источник ролей - AI/platform/роли_и_доступ.md.
"""
from __future__ import annotations

# (code, name, is_enabled, sort_order)
# Включаем только то, под что есть реальный модуль-страница. Остальные домены
# заведены в каталоге, но выключены - появятся в меню, когда будет их код.
DEFAULT_MODULES: list[tuple[str, str, bool, int]] = [
    ("sales", "Продажи", True, 10),
    ("presale", "Presale", False, 20),
    ("marketing", "Маркетинг", False, 30),
    ("bidding", "Торги на ЭП", False, 40),
    ("logistics", "Логистика", False, 50),
    ("docs", "Документооборот", False, 60),
]

# (code, name)
DEFAULT_ROLES: list[tuple[str, str]] = [
    ("mop", "Менеджер по продажам"),
    ("rop", "Руководитель отдела продаж"),
    ("marketer", "Маркетолог"),
    ("analyst", "Аналитик / админ"),
    ("security", "ИБ / аудит"),
    ("domain_head", "Руководитель домена"),
]

# Какие модули перечисляет роль (role_modules).
# - analyst особый: получает ВСЕ включённые модули (заполняется в сиде динамически,
#   поэтому здесь его нет);
# - security: аудит-лог - функция ядра, а не модуль, поэтому пусто;
# - domain_head: модуль(и) назначаются на конкретный домен при найме, поэтому пусто.
ROLE_MODULE_GRANTS: dict[str, list[str]] = {
    "mop": ["sales"],
    "rop": ["sales"],
    "marketer": ["marketing"],
    "domain_head": [],
    "security": [],
}

# Код роли-суперадмина, которой сид выдаёт все включённые модули.
SUPERADMIN_ROLE = "analyst"
