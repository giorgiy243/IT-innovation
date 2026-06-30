"""Личный кабинет сотрудника: свой профиль и смена пароля.

Все маршруты под /api/me и защищены get_current_auth (любой залогиненный
пользователь). tenant_id и user_id берутся ТОЛЬКО из серверной сессии, никогда
из запроса — сотрудник может управлять лишь собственной учёткой.

Профиль — это мастер-данные Employee (ими также управляет ИБ/админ через
/employees). Сотрудник правит свои личные поля, но не роль/доступ/руководителя.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DBSession

from core.auth.deps import get_current_auth
from core.auth.password_log import log_password_change
from core.auth.passwords import hash_password, password_policy_errors, verify_password
from core.auth.service import AuthContext
from core.db import get_db
from core.models import Employee, User

router = APIRouter(prefix="/api/me", tags=["me"])

# Поля Employee, которые сотрудник может править сам. Роль/доступ/руководитель
# сюда НЕ входят — они управляются ИБ/админом (мастер-данные доступа).
EDITABLE_FIELDS = (
    "last_name", "first_name", "middle_name", "position", "email",
    "phone_personal", "phone_work", "phone_extension",
)


class ProfileResponse(BaseModel):
    login: str
    must_change_password: bool
    password_changed_at: str | None
    roles: list[str]
    has_employee: bool
    full_name: str | None
    last_name: str | None
    first_name: str | None
    middle_name: str | None
    position: str | None
    email: str | None
    phone_personal: str | None
    phone_work: str | None
    phone_extension: str | None
    # Read-only: показываем, но не редактируем из кабинета.
    domain_name: str | None
    manager_name: str | None
    is_active: bool


class ProfileUpdateRequest(BaseModel):
    last_name: str | None = Field(default=None, max_length=150)
    first_name: str | None = Field(default=None, max_length=100)
    middle_name: str | None = Field(default=None, max_length=100)
    position: str | None = Field(default=None, max_length=255)
    email: str | None = Field(default=None, max_length=255)
    phone_personal: str | None = Field(default=None, max_length=50)
    phone_work: str | None = Field(default=None, max_length=50)
    phone_extension: str | None = Field(default=None, max_length=20)


class ChangeMyPasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=1, max_length=1024)


def _profile_response(db: DBSession, user: User) -> ProfileResponse:
    emp = user.employee
    roles = sorted(ur.role.name for ur in user.user_roles)
    manager_name: str | None = None
    if emp is not None and emp.manager_id is not None:
        mgr = db.get(Employee, emp.manager_id)
        manager_name = mgr.full_name if mgr is not None else None
    return ProfileResponse(
        login=user.login,
        must_change_password=user.must_change_password,
        password_changed_at=(
            user.password_changed_at.isoformat() if user.password_changed_at else None
        ),
        roles=roles,
        has_employee=emp is not None,
        full_name=emp.full_name if emp else None,
        last_name=emp.last_name if emp else None,
        first_name=emp.first_name if emp else None,
        middle_name=emp.middle_name if emp else None,
        position=emp.position if emp else None,
        email=emp.email if emp else None,
        phone_personal=emp.phone_personal if emp else None,
        phone_work=emp.phone_work if emp else None,
        phone_extension=emp.phone_extension if emp else None,
        domain_name=emp.domain_name if emp else None,
        manager_name=manager_name,
        is_active=emp.is_active if emp else True,
    )


@router.get("/profile", response_model=ProfileResponse)
def get_profile(
    db: DBSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth),
) -> ProfileResponse:
    """Профиль текущего пользователя: учётка + связанная карточка сотрудника."""
    user = db.get(User, auth.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Сессия недействительна")
    return _profile_response(db, user)


@router.patch("/profile", response_model=ProfileResponse)
def update_profile(
    payload: ProfileUpdateRequest,
    db: DBSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth),
) -> ProfileResponse:
    """Обновить редактируемые поля своей карточки сотрудника."""
    user = db.get(User, auth.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Сессия недействительна")
    emp = user.employee
    if emp is None:
        raise HTTPException(
            status_code=400, detail="Профиль не привязан к карточке сотрудника"
        )

    fields_set = payload.model_fields_set
    if "last_name" in fields_set:
        last_name = (payload.last_name or "").strip()
        if not last_name:
            raise HTTPException(status_code=400, detail="Фамилия не может быть пустой")
        emp.last_name = last_name

    for field in EDITABLE_FIELDS:
        if field == "last_name":
            continue  # уже обработано выше с валидацией
        if field in fields_set:
            value = getattr(payload, field)
            # Строку обрезаем; пустую трактуем как очистку необязательного поля.
            if isinstance(value, str):
                value = value.strip() or None
            setattr(emp, field, value)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=400, detail="Сотрудник с таким ФИО уже существует"
        )
    db.refresh(user)
    return _profile_response(db, user)


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_my_password(
    payload: ChangeMyPasswordRequest,
    response: Response,
    db: DBSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth),
) -> Response:
    """Сменить свой пароль из кабинета. Требует ввода текущего пароля."""
    user = db.get(User, auth.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Сессия недействительна")

    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Текущий пароль неверен")

    new_password = payload.new_password
    errors = password_policy_errors(new_password)
    if errors:
        raise HTTPException(status_code=400, detail="; ".join(errors))

    if verify_password(new_password, user.password_hash):
        raise HTTPException(
            status_code=400, detail="Новый пароль не должен совпадать со старым"
        )

    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    log_password_change(
        db,
        tenant_id=user.tenant_id,
        user=user,
        actor_user_id=user.id,
        actor_login=user.login,
        event="self_change",
    )
    db.commit()
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
