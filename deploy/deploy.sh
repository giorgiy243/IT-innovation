#!/usr/bin/env bash
# Деплой-конвейер: git pull -> миграции -> рестарт.
# Деплой только из ветки main. Запускать на сервере (prod).
# Staging: те же шаги, другой .env и systemd-юнит.
#
# Использование:
#   ./deploy/deploy.sh                        # prod (.env, юнит it-innovation)
#   ./deploy/deploy.sh .env.staging it-innovation-staging
set -euo pipefail

ENV_FILE="${1:-.env}"
UNIT_NAME="${2:-it-innovation}"

echo "[deploy] Загружаем окружение из $ENV_FILE"
set -a; source "$ENV_FILE"; set +a

echo "[deploy] Обновляем код из main"
git pull --ff-only origin main

echo "[deploy] Применяем миграции"
.venv/bin/alembic upgrade head

echo "[deploy] Перезапускаем сервис $UNIT_NAME"
systemctl restart "$UNIT_NAME"

echo "[deploy] Проверяем /health"
sleep 2
PORT="${APP_PORT:-8000}"
curl -sf "http://127.0.0.1:${PORT}/health" | python3 -c "import sys,json; d=json.load(sys.stdin); print('[deploy] OK:', d)"
