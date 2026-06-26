$root = Split-Path -Parent $PSScriptRoot
$pidFile = "$root\.uvicorn.pid"

if (-not (Test-Path $pidFile)) {
    Write-Host "[IT-innovation] Не запущен"
    exit 0
}

$savedPid = [int](Get-Content $pidFile -Raw).Trim()
$proc = Get-Process -Id $savedPid -ErrorAction SilentlyContinue

if ($proc) {
    Stop-Process -Id $savedPid -Force
    Write-Host "[IT-innovation] Остановлен (PID $savedPid)"
} else {
    Write-Host "[IT-innovation] Процесс $savedPid уже не работал"
}

Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
