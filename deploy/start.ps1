$root = Split-Path -Parent $PSScriptRoot
$pidFile = "$root\.uvicorn.pid"

# Уже запущен? Проверяем по реальному процессу uvicorn core.app (не только pidfile -
# осиротевший процесс мог пережить pidfile и держать порт со старым кодом).
$running = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*uvicorn*core.app:app*' })
if ($running.Count -gt 0) {
    Write-Host "[IT-innovation] Уже запущен (PID $($running.ProcessId -join ',')) -> http://127.0.0.1:8020"
    Start-Process "http://127.0.0.1:8020"
    exit 0
}
if (Test-Path $pidFile) { Remove-Item $pidFile -Force }

# Порт занят посторонним процессом? Не стартуем поверх - иначе health-check ниже
# ответит чужой процесс и мы получим ложное «OK» со старым кодом.
$busy = Get-NetTCPConnection -LocalPort 8020 -State Listen -ErrorAction SilentlyContinue
if ($busy) {
    Write-Host "[IT-innovation] Порт 8020 занят (PID $($busy.OwningProcess -join ',')). Освободи: deploy\stop.ps1"
    exit 1
}

# Загружаем переменные из .env
Get-Content "$root\.env" | ForEach-Object {
    if ($_ -match "^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$") {
        [System.Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], "Process")
    }
}

Write-Host "[IT-innovation] Запуск на http://0.0.0.0:8020 (доступ из локальной сети) ..."

# host 0.0.0.0 - сервер доступен по IP машины из локальной сети, порт 8020.
$proc = Start-Process `
    -FilePath "$root\.venv\Scripts\uvicorn.exe" `
    -ArgumentList "core.app:app", "--host", "0.0.0.0", "--port", "8020" `
    -WorkingDirectory $root `
    -WindowStyle Hidden `
    -PassThru

$proc.Id | Out-File -FilePath $pidFile -Encoding utf8 -NoNewline

Start-Sleep -Seconds 2
try {
    $resp = Invoke-RestMethod "http://127.0.0.1:8020/health" -ErrorAction Stop
    Write-Host "[IT-innovation] OK - статус: $($resp.status), env: $($resp.env)"
} catch {
    Write-Host "[IT-innovation] Сервер не ответил на /health - проверь логи"
}

Start-Process "http://127.0.0.1:8020"
