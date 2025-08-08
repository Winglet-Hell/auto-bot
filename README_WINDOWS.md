## Запуск на Windows (без EXE)

1) Установите Python 3.12 (без админа можно через Microsoft Store)
2) Откройте PowerShell и выполните:

```
cd "C:\Users\Stepin\Documents"  # путь к папке с проектом
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m playwright install chromium
python .\trans_saver.py
```

Если активация виртуалки заблокирована политикой:
```
Set-ExecutionPolicy -Scope Process Bypass
.\.venv\Scripts\Activate.ps1
```

Подсказки:
- Для локального адреса `smartplayer1.neftm.local` убедитесь, что он резолвится (при необходимости добавьте в hosts).
- Первое `python -m playwright install chromium` скачает Chromium; далее не требуется.
