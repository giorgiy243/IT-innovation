# Локальный запуск для разработки (Windows).
# Prod: .\deploy\run_local.ps1
# Staging: .\deploy\run_local.ps1 staging
param([string]$Env = "prod")

$envFile = if ($Env -eq "staging") { ".env.staging" } else { ".env" }
$port    = if ($Env -eq "staging") { "8001" } else { "8000" }

Write-Host "[run_local] Окружение: $Env | .env: $envFile | порт: $port"

Get-Content $envFile | ForEach-Object {
    if ($_ -match "^\s*([^#][^=]+)=(.*)$") {
        [System.Environment]::SetEnvironmentVariable($Matches[1].Trim(), $Matches[2].Trim(), "Process")
    }
}

& ".venv\Scripts\alembic.exe" upgrade head
& ".venv\Scripts\uvicorn.exe" core.app:app --host 127.0.0.1 --port $port --reload
