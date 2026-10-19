# Отключение автозапуска Ollama (ярлык в Startup) для текущего пользователя.
# Используется тот же механизм, что «Диспетчер задач → Автозагрузка»:
# HKCU\...\Explorer\StartupApproved\StartupFolder.
#
# Запуск:
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools/disable_ollama_autostart.ps1
#
# Включить обратно: заменить 03 на 02 в значении Ollama.lnk.
$ErrorActionPreference = 'Stop'
$name = 'Ollama.lnk'
$startup = [Environment]::GetFolderPath('Startup')
$link = Join-Path $startup $name
if (-not (Test-Path -LiteralPath $link)) {
    Write-Output "Ярлык $name не найден в $startup — автозапуск уже отключён/не настроен."
    exit 0
}
$key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder'
if (-not (Test-Path $key)) {
    New-Item -Path $key -Force | Out-Null
}
$disabled = [byte[]](3, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
Set-ItemProperty -Path $key -Name $name -Value $disabled -Type Binary -Force
$value = (Get-ItemProperty $key).$name
Write-Output ("Автозапуск $name отключён: " +
    (($value | ForEach-Object { $_.ToString('X2') }) -join ' '))
