## Запуск на Windows (без EXE)

1) Установите Python 3.12 (без админа можно через Microsoft Store)
2) Откройте PowerShell и выполните:

```
cd "C:\path\to\auto-bot"  # путь к папке с проектом
scripts\windows\run_bot.bat
```

Если активация виртуалки заблокирована политикой:
```
Set-ExecutionPolicy -Scope Process Bypass
.\.venv\Scripts\Activate.ps1
```

Подсказки:
- Для локального адреса `smartplayer1.neftm.local` убедитесь, что он резолвится (при необходимости добавьте в hosts).
- Первый запуск скачает Chromium автоматически; далее не требуется.


