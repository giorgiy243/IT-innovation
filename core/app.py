"""Точка входа FastAPI.

Фаза 0.4 - hello-эндпоинт и проверка деплой-конвейера.
Фаза 1.1 - подключена аутентификация (/login, /logout, /me).
Дальше: RBAC, реестр модулей, маршруты доменов.
"""
import os
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from core.auth.routes import router as auth_router

app = FastAPI(title="IT-innovation", version="0.1.0")

app.include_router(auth_router)


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "env": os.getenv("APP_ENV", "unknown")})


@app.get("/")
async def root() -> JSONResponse:
    return JSONResponse({"message": "IT-innovation platform. Фаза 0.4 - каркас работает."})
