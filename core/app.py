"""Точка входа FastAPI. Фаза 0.4 - hello-эндпоинт для проверки деплой-конвейера.

Фаза 1: подключить auth, RBAC, реестр модулей, маршруты доменов.
"""
import os
from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI(title="IT-innovation", version="0.0.1")


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "env": os.getenv("APP_ENV", "unknown")})


@app.get("/")
async def root() -> JSONResponse:
    return JSONResponse({"message": "IT-innovation platform. Фаза 0.4 - каркас работает."})
