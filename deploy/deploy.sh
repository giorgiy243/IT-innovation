#!/usr/bin/env bash
# Скелет деплоя (подфаза 0.3). Наполняется в подфазе 0.4 (сервер и окружения).
#
# Конвейер: git pull -> миграции -> рестарт. Деплой только из ветки main.
# Запускать на внутреннем сервере. prod и staging - разные порты/БД/.env.
set -euo pipefail

echo "[deploy] Скелет. Реализация - подфаза 0.4."
# git pull --ff-only origin main
# .venv/bin/alembic upgrade head
# systemctl restart it-innovation     # uvicorn под systemd
