"""Точка входа FastAPI.

Фаза 0.4 - hello-эндпоинт и проверка деплой-конвейера.
Фаза 1.1 - подключена аутентификация (/login, /logout, /me).
Фаза 1.3 - подключён RBAC: навигация по ролям (/nav).
Дальше: реестр модулей, маршруты доменов.
"""
import os
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from core.admin.routes import router as admin_router
from core.auth.routes import router as auth_router
from core.client_rotation.routes import router as client_rotation_router
from core.me.routes import router as me_router
from core.pages import router as pages_router
from core.rbac.routes import router as rbac_router
from core.vendors.routes import router as vendors_router

app = FastAPI(title="IT-innovation", version="0.1.0")

app.include_router(pages_router)
app.include_router(auth_router)
app.include_router(rbac_router)
app.include_router(vendors_router)
app.include_router(admin_router)
app.include_router(client_rotation_router)
app.include_router(me_router)


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "env": os.getenv("APP_ENV", "unknown")})


@app.get("/")
async def root() -> JSONResponse:
    return JSONResponse({"message": "IT-innovation platform. Фаза 0.4 - каркас работает."})
