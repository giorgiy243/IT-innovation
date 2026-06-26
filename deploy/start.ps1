$root = Split-Path -Parent $PSScriptRoot
$pidFile = "$root\.uvicorn.pid"

# Проверяем - уже запущен?
if (Test-Path $pidFile) {
    $savedPid = [int](Get-Content $pidFile -Raw).Trim()
    $proc = Get-Process -Id $savedPid -ErrorAction SilentlyContinue
    if ($proc) {
        Write-Host "[IT-innovation] Уже запущен (PID $savedPid) -> http://127.0.0.1:8000"
        Start-Process "http://127.0.0.1:8000"
        exit 0
    }
    Remove-Item $pidFile -Force
}

# Загружаем переменные из .env
Get-Content "$root\.env" | ForEach-Object {
    if ($_ -match "^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$") {
        [System.Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], "Process")
    }
}

Write-Host "[IT-innovation] Запуск на http://127.0.0.1:8000 ..."

$proc = Start-Process `
    -FilePath "$root\.venv\Scripts\uvicorn.exe" `
    -ArgumentList "core.app:app", "--host", "127.0.0.1", "--port", "8000" `
    -WorkingDirectory $root `
    -WindowStyle Hidden `
    -PassThru

$proc.Id | Out-File -FilePath $pidFile -Encoding utf8 -NoNewline

Start-Sleep -Seconds 2
try {
    $resp = Invoke-RestMethod "http://127.0.0.1:8000/health" -ErrorAction Stop
    Write-Host "[IT-innovation] OK - статус: $($resp.status), env: $($resp.env)"
} catch {
    Write-Host "[IT-innovation] Сервер не ответил на /health - проверь логи"
}

Start-Process "http://127.0.0.1:8000"
