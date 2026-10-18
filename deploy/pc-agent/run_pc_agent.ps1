# Запуск ПК-агента (Windows) — Задача 1.
# Копирует движок сильной модели ПК (llama-server) на VPS через reverse-SSH
# и показывает окно-подтверждение. Настройте параметры ниже под себя.

$ErrorActionPreference = "Stop"

# ---- Параметры (измените под свою среду) ----------------------------------
$Vps          = "user@example.com"     # пользователь@VPS для ssh
$Identity     = "$env:USERPROFILE\.ssh\rtk_pc"  # приватный ключ
$EnginePort   = 8080                   # порт llama-server ПК (сильная модель)
$TunnelPort   = 18080                  # порт движка на VPS (loopback)
$AgentPort    = 18081                  # служебный порт агента (loopback)
$OllamaPort   = 11434                  # локальный порт Ollama ПК
$EmbedPort    = 11435                  # порт Ollama на VPS (loopback)
$ModelsRoot   = "D:\models"            # папка ПК с моделями/серверами

# ---- Рабочая папка = корень репозитория rtk ---------------------------------
$Repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $Repo

python -m rtk_pc_agent `
    --vps $Vps `
    --identity $Identity `
    --engine-port $EnginePort `
    --tunnel-port $TunnelPort `
    --agent-port $AgentPort `
    --ollama-port $OllamaPort `
    --embed-tunnel-port $EmbedPort `
    --root $ModelsRoot
