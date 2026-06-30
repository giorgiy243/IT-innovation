$root = Split-Path -Parent $PSScriptRoot
$pidFile = "$root\.uvicorn.pid"

# Гасим ВСЕ процессы uvicorn core.app:app, а не только PID из pidfile.
# Иначе «осиротевший» процесс может пережить остановку, держать порт и
# отдавать устаревший код (после рестарта health-check отвечает он -> ложное «OK»).
$procs = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*uvicorn*core.app:app*' })

if ($procs.Count -gt 0) {
    foreach ($p in $procs) {
        # Дочерний процесс мог уже умереть вместе с родителем - проверяем перед kill,
        # чтобы не словить «process not found» и не уронить exit-код.
        if (Get-Process -Id $p.ProcessId -ErrorAction SilentlyContinue) {
            Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
            Write-Host "[IT-innovation] Остановлен PID $($p.ProcessId)"
        }
    }
} else {
    Write-Host "[IT-innovation] Не запущен (процессов uvicorn core.app не найдено)"
}

Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
exit 0
