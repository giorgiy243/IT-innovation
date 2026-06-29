"""API управления пользователями. Доступ: только роль security.

GET  /api/admin/users           - список пользователей арендатора с ролями
POST /api/admin/users           - создать пользователя, опционально назначить роль
PATCH /api/admin/users/{id}     - деактивировать / активировать пользователя

tenant_id берётся из серверной сессии. Пароль хешируется argon2, в открытом
виде не хранится и не возвращается.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from core.auth.deps import get_current_auth
from core.auth.passwords import hash_password
from core.auth.service import AuthContext
from core.db import get_db
from core.models import Employee, EmployeePosition, Role, SCOPE_VALUES, User, UserRole
from core.rbac.deps import get_access
from core.rbac.service import AccessProfile

router = APIRouter(prefix="/api/admin", tags=["admin"])

MIN_PASSWORD_LEN = 8

ROLE_LABELS: dict[str, str] = {
    "mop": "МОП",
    "rop": "РОП",
    "marketer": "Маркетолог",
    "presale_engineer": "Пресейл",
    "analyst": "Аналитик",
    "security": "ИБ / Аудит",
    "domain_head": "Рук. домена",
}

SCOPE_LABELS: dict[str, str] = {
    "own": "Свои данные",
    "team": "Команда",
    "domain": "Домен",
    "all": "Все данные",
}


def _require_security(access: AccessProfile = Depends(get_access)) -> AccessProfile:
    if "security" not in access.role_codes:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Нет доступа: требуется роль ИБ/аудит",
        )
    return access


class RoleInfo(BaseModel):
    code: str
    name: str
    scope: str


class UserItem(BaseModel):
    id: int
    login: str
    is_active: bool
    created_at: str
    roles: list[RoleInfo]


class CreateUserRequest(BaseModel):
    login: str = Field(min_length=1, max_length=150)
    password: str = Field(min_length=8, max_length=1024)
    role_code: str | None = None
    scope: str = "own"


class PatchUserRequest(BaseModel):
    is_active: bool


class RoleOption(BaseModel):
    code: str
    name: str


class PositionItem(BaseModel):
    id: int
    name: str


class CreatePositionRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class EmployeeItem(BaseModel):
    id: int
    full_name: str
    last_name: str
    first_name: str | None
    middle_name: str | None
    role_code: str | None
    position: str | None
    email: str | None
    phone_personal: str | None
    phone_work: str | None
    phone_extension: str | None
    domain_name: str | None
    manager_id: int | None
    is_active: bool
    has_account: bool


class CreateEmployeeRequest(BaseModel):
    last_name: str = Field(min_length=1, max_length=150)
    first_name: str | None = Field(default=None, max_length=100)
    middle_name: str | None = Field(default=None, max_length=100)
    role_code: str | None = Field(default=None, max_length=50)
    position: str | None = Field(default=None, max_length=255)
    email: str | None = Field(default=None, max_length=255)
    phone_personal: str | None = Field(default=None, max_length=50)
    phone_work: str | None = Field(default=None, max_length=50)
    phone_extension: str | None = Field(default=None, max_length=20)
    domain_name: str | None = Field(default=None, max_length=255)
    manager_id: int | None = None


class UpdateEmployeeRequest(BaseModel):
    last_name: str | None = Field(default=None, max_length=150)
    first_name: str | None = Field(default=None, max_length=100)
    middle_name: str | None = Field(default=None, max_length=100)
    role_code: str | None = None
    position: str | None = None
    email: str | None = None
    phone_personal: str | None = None
    phone_work: str | None = None
    phone_extension: str | None = None
    domain_name: str | None = None
    manager_id: int | None = None
    is_active: bool | None = None


def _employee_to_item(e: Employee) -> EmployeeItem:
    return EmployeeItem(
        id=e.id,
        full_name=e.full_name,
        last_name=e.last_name,
        first_name=e.first_name,
        middle_name=e.middle_name,
        role_code=e.role_code,
        position=e.position,
        email=e.email,
        phone_personal=e.phone_personal,
        phone_work=e.phone_work,
        phone_extension=e.phone_extension,
        domain_name=e.domain_name,
        manager_id=e.manager_id,
        is_active=e.is_active,
        has_account=bool(e.users),
    )


def _require_security_or_analyst(access: AccessProfile = Depends(get_access)) -> AccessProfile:
    if not ({"security", "analyst"} & set(access.role_codes)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нет доступа")
    return access


@router.get("/roles", response_model=list[RoleOption])
def list_roles(
    db: DBSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth),
) -> list[RoleOption]:
    roles = (
        db.execute(select(Role).where(Role.tenant_id == auth.tenant_id).order_by(Role.name))
        .scalars().all()
    )
    return [RoleOption(code=r.code, name=r.name) for r in roles]


@router.get("/positions", response_model=list[PositionItem])
def list_positions(
    db: DBSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth),
) -> list[PositionItem]:
    positions = (
        db.execute(
            select(EmployeePosition)
            .where(EmployeePosition.tenant_id == auth.tenant_id)
            .order_by(EmployeePosition.name)
        )
        .scalars().all()
    )
    return [PositionItem(id=p.id, name=p.name) for p in positions]


@router.post("/positions", response_model=PositionItem, status_code=status.HTTP_201_CREATED)
def create_position(
    payload: CreatePositionRequest,
    db: DBSession = Depends(get_db),
    access: AccessProfile = Depends(_require_security_or_analyst),
) -> PositionItem:
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Название не может быть пустым")
    existing = db.execute(
        select(EmployeePosition).where(
            EmployeePosition.tenant_id == access.tenant_id,
            EmployeePosition.name == name,
        )
    ).scalar_one_or_none()
    if existing:
        return PositionItem(id=existing.id, name=existing.name)
    pos = EmployeePosition(tenant_id=access.tenant_id, name=name)
    db.add(pos)
    db.commit()
    db.refresh(pos)
    return PositionItem(id=pos.id, name=pos.name)


@router.get("/employees", response_model=list[EmployeeItem])
def list_employees(
    db: DBSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth),
) -> list[EmployeeItem]:
    employees = (
        db.execute(
            select(Employee)
            .where(Employee.tenant_id == auth.tenant_id)
            .order_by(Employee.last_name, Employee.first_name)
        )
        .scalars()
        .all()
    )
    return [_employee_to_item(e) for e in employees]


@router.post("/employees", response_model=EmployeeItem, status_code=status.HTTP_201_CREATED)
def create_employee(
    payload: CreateEmployeeRequest,
    db: DBSession = Depends(get_db),
    access: AccessProfile = Depends(_require_security),
) -> EmployeeItem:
    last_name = payload.last_name.strip()
    if not last_name:
        raise HTTPException(status_code=400, detail="Фамилия обязательна")

    if payload.manager_id is not None:
        mgr = db.get(Employee, payload.manager_id)
        if mgr is None or mgr.tenant_id != access.tenant_id:
            raise HTTPException(status_code=400, detail="Руководитель не найден")

    emp = Employee(
        tenant_id=access.tenant_id,
        last_name=last_name,
        first_name=payload.first_name or None,
        middle_name=payload.middle_name or None,
        role_code=payload.role_code or None,
        position=payload.position or None,
        email=payload.email or None,
        phone_personal=payload.phone_personal or None,
        phone_work=payload.phone_work or None,
        phone_extension=payload.phone_extension or None,
        domain_name=payload.domain_name or None,
        manager_id=payload.manager_id,
    )
    db.add(emp)
    db.commit()
    db.refresh(emp)
    return _employee_to_item(emp)


@router.patch("/employees/{emp_id}", response_model=EmployeeItem)
def update_employee(
    emp_id: int,
    payload: UpdateEmployeeRequest,
    db: DBSession = Depends(get_db),
    access: AccessProfile = Depends(_require_security),
) -> EmployeeItem:
    emp = db.get(Employee, emp_id)
    if emp is None or emp.tenant_id != access.tenant_id:
        raise HTTPException(status_code=404, detail="Сотрудник не найден")

    fields_set = payload.model_fields_set

    if "last_name" in fields_set:
        last_name = (payload.last_name or "").strip()
        if not last_name:
            raise HTTPException(status_code=400, detail="Фамилия не может быть пустой")
        emp.last_name = last_name

    if "manager_id" in fields_set and payload.manager_id is not None:
        if payload.manager_id == emp_id:
            raise HTTPException(status_code=400, detail="Сотрудник не может быть своим руководителем")
        mgr = db.get(Employee, payload.manager_id)
        if mgr is None or mgr.tenant_id != access.tenant_id:
            raise HTTPException(status_code=400, detail="Руководитель не найден")

    for field in (
        "first_name", "middle_name", "role_code", "position", "email",
        "phone_personal", "phone_work", "phone_extension",
        "domain_name", "manager_id", "is_active",
    ):
        if field in fields_set:
            setattr(emp, field, getattr(payload, field))

    db.commit()
    db.refresh(emp)
    return _employee_to_item(emp)


@router.delete("/employees/{emp_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_employee(
    emp_id: int,
    db: DBSession = Depends(get_db),
    access: AccessProfile = Depends(_require_security),
) -> None:
    emp = db.get(Employee, emp_id)
    if emp is None or emp.tenant_id != access.tenant_id:
        raise HTTPException(status_code=404, detail="Сотрудник не найден")
    db.delete(emp)
    db.commit()


@router.post("/employees/{emp_id}/grant-access", response_model=EmployeeItem)
def grant_access(
    emp_id: int,
    db: DBSession = Depends(get_db),
    access: AccessProfile = Depends(_require_security),
) -> EmployeeItem:
    """Выдать сотруднику учётную запись для входа.

    Логин = доменное имя, временный пароль = добавочный номер. Пользователь
    помечается must_change_password=True - при первом входе платформа потребует
    сменить временный пароль на постоянный. Роль берётся из карточки (role_code).
    """
    emp = db.get(Employee, emp_id)
    if emp is None or emp.tenant_id != access.tenant_id:
        raise HTTPException(status_code=404, detail="Сотрудник не найден")
    if emp.users:
        raise HTTPException(status_code=400, detail="Доступ уже выдан")

    domain = (emp.domain_name or "").strip()
    ext = (emp.phone_extension or "").strip()
    if not domain:
        raise HTTPException(status_code=400, detail="Не указано доменное имя сотрудника")
    if not ext:
        raise HTTPException(status_code=400, detail="Не указан добавочный номер сотрудника")

    existing = db.execute(
        select(User).where(User.tenant_id == access.tenant_id, User.login == domain)
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=400, detail=f"Логин '{domain}' уже занят")

    user = User(
        tenant_id=access.tenant_id,
        login=domain,
        password_hash=hash_password(ext),
        must_change_password=True,
        employee_id=emp.id,
    )
    db.add(user)
    db.flush()

    if emp.role_code:
        role = db.execute(
            select(Role).where(
                Role.tenant_id == access.tenant_id, Role.code == emp.role_code
            )
        ).scalar_one_or_none()
        if role is not None:
            db.add(UserRole(user_id=user.id, role_id=role.id, scope="own"))

    db.commit()
    db.refresh(emp)
    return _employee_to_item(emp)


@router.delete("/employees/{emp_id}/grant-access", response_model=EmployeeItem)
def revoke_access(
    emp_id: int,
    db: DBSession = Depends(get_db),
    access: AccessProfile = Depends(_require_security),
) -> EmployeeItem:
    """Забрать у сотрудника доступ: удалить его учётную запись(и).

    Удаление User каскадом снимает его сессии и роли (см. модели) - сотрудник
    немедленно теряет вход. Запрет на самоотзыв: нельзя удалить собственную
    учётку (защита от блокировки самого себя).
    """
    emp = db.get(Employee, emp_id)
    if emp is None or emp.tenant_id != access.tenant_id:
        raise HTTPException(status_code=404, detail="Сотрудник не найден")
    if not emp.users:
        raise HTTPException(status_code=400, detail="Доступ не выдан")
    if any(u.id == access.user_id for u in emp.users):
        raise HTTPException(status_code=400, detail="Нельзя забрать доступ у самого себя")

    for user in list(emp.users):
        db.delete(user)
    db.commit()
    db.refresh(emp)
    return _employee_to_item(emp)


def _user_to_item(u: User) -> UserItem:
    roles = [
        RoleInfo(code=ur.role.code, name=ur.role.name, scope=ur.scope)
        for ur in sorted(u.user_roles, key=lambda r: r.role.code)
    ]
    return UserItem(
        id=u.id,
        login=u.login,
        is_active=u.is_active,
        created_at=u.created_at.isoformat(),
        roles=roles,
    )


@router.get("/users", response_model=list[UserItem])
def list_users(
    db: DBSession = Depends(get_db),
    access: AccessProfile = Depends(_require_security),
) -> list[UserItem]:
    users = (
        db.execute(
            select(User)
            .where(User.tenant_id == access.tenant_id)
            .order_by(User.created_at)
        )
        .scalars()
        .all()
    )
    return [_user_to_item(u) for u in users]


@router.post("/users", response_model=UserItem, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: CreateUserRequest,
    db: DBSession = Depends(get_db),
    access: AccessProfile = Depends(_require_security),
) -> UserItem:
    login = payload.login.strip()
    if not login:
        raise HTTPException(status_code=400, detail="Пустой логин")

    existing = db.execute(
        select(User).where(User.tenant_id == access.tenant_id, User.login == login)
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=400, detail=f"Логин '{login}' уже занят")

    user = User(
        tenant_id=access.tenant_id,
        login=login,
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    db.flush()

    if payload.role_code:
        scope = payload.scope if payload.scope in SCOPE_VALUES else "own"
        role = db.execute(
            select(Role).where(
                Role.tenant_id == access.tenant_id,
                Role.code == payload.role_code,
            )
        ).scalar_one_or_none()
        if role is None:
            raise HTTPException(status_code=400, detail=f"Роль '{payload.role_code}' не найдена")
        db.add(UserRole(user_id=user.id, role_id=role.id, scope=scope))
        db.flush()

    db.commit()
    db.refresh(user)
    return _user_to_item(user)


@router.patch("/users/{user_id}", response_model=UserItem)
def patch_user(
    user_id: int,
    payload: PatchUserRequest,
    db: DBSession = Depends(get_db),
    access: AccessProfile = Depends(_require_security),
) -> UserItem:
    user = db.get(User, user_id)
    if user is None or user.tenant_id != access.tenant_id:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    user.is_active = payload.is_active
    db.commit()
    db.refresh(user)
    return _user_to_item(user)
