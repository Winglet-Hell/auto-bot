## Auto Bot — помощник для сохранения карточек в SmartPlayer CMS

Инструмент на Python + Playwright, который помогает быстро открывать карточки трансляций в SmartPlayer CMS, нажимать «Сохранить» и переходить к следующей. Есть простой GUI на Tkinter с выбором пресетов, настройкой скорости и авторизацией (авто/ручной вход).

### Возможности
- Авто‑вход по `SP_USERNAME`/`SP_PASSWORD` или ручной вход
- Безопасное хранение сессии (`var/state.json`)
- Поиск и нажатие кнопки «Сохранить» с несколькими стратегиями
- Поддержка RU/EN интерфейса
- GUI‑панель: пресеты, URL, логин/пароль, скорость выполнения
- Визуальная подсветка кликов (можно отключить)

### Требования
- Python 3.12+
- Chromium (ставится автоматически через Playwright)
- macOS/Windows/Linux

### Быстрый старт
- macOS: см. `docs/QUICK_START_MAC.md`
- Windows: см. `docs/README_WINDOWS.md`

Кроссплатформенно можно воспользоваться готовыми скриптами запуска:

```bash
# macOS / Linux (первый старт и повторные)
./scripts/run_bot.sh

# macOS / Linux (если всё уже установлено)
./scripts/run_only.sh

# Windows (первый старт и повторные)
scripts/windows/run_bot.bat

# Windows (если всё уже установлено)
scripts/windows/run_only.bat
```

### Использование
1) Запустите скрипт для вашей ОС (см. выше)
2) В GUI выберите пресет или укажите `Start URL`, логин и пароль
3) Нажмите «Запустить». Окно можно оставить открытым и менять скорость исполнения

Сохранение сессии: после успешного входа создаётся файл `var/state.json`. При следующем запуске вход выполняется автоматически (если не задан форс‑вход или новые креды).

### Переменные окружения (.env)
Можно создать файл на основе `env.example` и переименовать его в `.env`, либо экспортировать переменные перед запуском.

- SP_USERNAME / SP_PASSWORD — учётные данные для авто‑логина
- FORCE_LOGIN — `1` чтобы игнорировать сохранённую сессию и войти заново
- SPEED — множитель скорости выполнения (по умолчанию `1.0`)
- WAIT_UNTIL_CARDS — `1` ждать появления карточек, `0` — не ждать
- POLL_INTERVAL_MS — период опроса при ожидании карточек (по умолчанию `1500`)
- MAX_WAIT_SECONDS — лимит ожидания (сек), `0` — без лимита
- SHOW_CLICKS — `1` подсвечивать клики, `0` — нет
- HIGHLIGHT_MS / HIGHLIGHT_COLOR — параметры подсветки
- HOLD_OPEN_SECONDS — держать окно открытым после завершения (сек)
- GRID_SCROLL_SELECTOR, CARD_TITLE_SELECTOR, TILE_CONTAINER_SELECTOR, TILE_PREVIEW_SELECTOR — переопределение селекторов
- SP_USERNAME_SELECTOR / SP_PASSWORD_SELECTOR / SP_SUBMIT_SELECTOR — переопределение селекторов формы логина

### Скрипты
- macOS/Linux: `scripts/run_bot.sh`, `scripts/run_only.sh` или из macOS‑папки `scripts/macos/run.sh` (двойной клик `scripts/macos/run.command`)
- Windows: `scripts/windows/run_bot.bat`, `scripts/windows/run_only.bat`

### Структура проекта
```
auto-bot/
  src/
    auto_bot/
      __init__.py
      app.py
  scripts/
    run_bot.sh
    run_only.sh
    macos/
      run.sh
      run.command
    windows/
      run_bot.bat
      run_only.bat
  docs/
    QUICK_START_MAC.md
    README_WINDOWS.md
  .github/
    ISSUE_TEMPLATE/
    workflows/ci.yml
  .editorconfig
  .gitignore
  LICENSE
  README.md
  requirements.txt
  requirements-dev.txt
  var/            # runtime артефакты (state.json), игнорируется
```

### Разработка
```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
# опционально: инструменты разработки
pip install -r requirements-dev.txt  # ruff и др.
```

Проверка стиля и синтаксиса локально:
```bash
ruff check .
python -m compileall src/auto_bot
```

### Безопасность
- Никогда не коммитьте реальные логины/пароли
- Файлы `var/state.json` и `.env` игнорируются в `.gitignore`

### Лицензия
MIT — см. `LICENSE`.

### Вклад
PR приветствуются! См. `CONTRIBUTING.md` и `CODE_OF_CONDUCT.md`.


