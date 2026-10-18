# ПК-агент оператора (Windows) — Задача 1

Эта инструкция — для **личного компьютера оператора** (Windows) с мощной LLM.
ПК подключается к VPS по SSH и пробрасывает свой движок на VPS (reverse-туннель),
чтобы `rtk-web` мог отвечать **сильной** моделью (`Qwen-9B`), а не слабой
`Qwen-2B`. Полное ТЗ — [`docs/task1-local-llm-from-pc.md`](../../docs/task1-local-llm-from-pc.md).

## Что нужно на ПК

1. **Windows 10/11** с встроенным OpenSSH-клиентом (`ssh.exe` в PATH).
   Проверка: `ssh -V`.
2. **Python 3.8+**.
3. **llama-server.exe** (llama.cpp), уже запущенный со **сильной** моделью на
   `127.0.0.1:8080`:
   ```
   llama-server.exe -m D:\models\Qwen-9B.gguf --port 8080 --host 127.0.0.1
   ```
4. **SSH-ключ** к VPS (без пароля).

## 1. Ключ SSH (однократно)

```powershell
ssh-keygen -t ed25519 -f $env:USERPROFILE\.ssh\rtk_pc -C "rtk-pc"
# Публичную часть добавьте на VPS в ~rtk/.ssh/authorized_keys (см. ниже)
Get-Content $env:USERPROFILE\.ssh\rtk_pc.pub
```

На VPS в `~/.ssh/authorized_keys` (пользователь, от которого идёт туннель)
добавьте строку с **ограничениями** (только loopback-форвардинг):

```
restrict,no-pty,no-agent-forwarding,no-X11-forwarding,permitlisten="127.0.0.1:18080",permitlisten="127.0.0.1:18081",permitlisten="127.0.0.1:11435" ssh-ed25519 AAAA... rtk-pc
```

## 2. Запуск ПК-агента

```powershell
python -m rtk_pc_agent `
  --vps rtk@<VPS-IP> `
  --identity $env:USERPROFILE\.ssh\rtk_pc `
  --engine-port 8080 `
  --tunnel-port 18080 `
  --agent-port 18081 `
  --ollama-port 11434 `
  --embed-tunnel-port 11435 `
  --root D:\models
```

Что произойдёт:

1. Поднимется служебный API агента на `127.0.0.1:18081` (только loopback).
2. Появится **окно-подтверждение**: «VPS `<IP>` просит подключить локальную
   LLM. Разрешить?» — нажмите **Да** (запоминается до конца SSH-сессии).
3. Поднимется reverse-туннель из **трёх** портов:
   - движок ПК → `127.0.0.1:18080` на VPS (сильная модель);
   - служебный API агента → `127.0.0.1:18081` на VPS;
   - Ollama ПК `127.0.0.1:11434` → `127.0.0.1:11435` на VPS
     (эмбеддинги RAG `nomic-embed-text-v2-moe`).

> Ollama должна быть запущена на ПК. Проверка:
> `curl http://127.0.0.1:11434/api/tags` — в списке должна быть
> `nomic-embed-text-v2-moe`.

Готовый запуск — скрипт [`run_pc_agent.ps1`](run_pc_agent.ps1).

## 3. Автозагрузка (Планировщик задач)

Создайте задачу «При входе пользователя», запускающую
`run_pc_agent.ps1` (от текущего пользователя, не от SYSTEM — нужен доступ к
папке моделей и к окну-подтверждению).

```powershell
$action  = New-ScheduledTaskAction -Execute "powershell.exe" `
  -Argument "-ExecutionPolicy Bypass -File `"$PWD\run_pc_agent.ps1`""
$trigger = New-ScheduledTaskTrigger -AtLogOn
Register-ScheduledTask -TaskName "RTK PC Agent" -Action $action -Trigger $trigger
```

## 4. Проверка

На **VPS**:

```bash
# Служебный API агента доступен через туннель?
curl -s http://127.0.0.1:18081/health
curl -s http://127.0.0.1:18081/confirm_status

# Движок ПК виден как OpenAI-совместимый?
curl -s -H "Authorization: Bearer $RTK_LOCAL_API_KEY" http://127.0.0.1:18080/v1/models

# Ollama ПК (эмбеддинги RAG) видна через туннель?
curl -s http://127.0.0.1:11435/api/tags
```

В UI чата (раздел «Локальная модель») переключатель **«Сильная (ПК)»**
должен стать активным, а имя модели — `Qwen-9B`.

## Безопасность

- ПК **не** публикуется в интернет: соединение исходящее ПК→VPS.
- Reverse-туннель слушает **только loopback** VPS
  (`127.0.0.1:18080/18081/11435`).
- Ключ ограничен `permitlisten` (разрешены только эти три порта).
- Подтверждение оператора обязательно перед поднятием туннеля.
- Приватный ключ — в `%USERPROFILE%\.ssh\` (ACL только владелец).
