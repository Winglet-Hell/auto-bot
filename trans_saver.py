import asyncio
import os
import time
from urllib.parse import urlsplit, urlunsplit
from playwright.async_api import async_playwright, TimeoutError as PWTimeout
from threading import Thread

# Простой логгер
def log(message: str) -> None:
    print(f"[bot] {message}", flush=True)

# Поддержка .env (необязательно). Если нет библиотеки, просто игнорируем
try:
    from dotenv import load_dotenv  # type: ignore

    load_dotenv()
except Exception:
    pass

# === Настройки ===
START_URL = "https://cms.smartplayer.org/#/broadcasts?folderId=602"  # <-- подставь свою ссылку

# Иконка для входа в карточку (поддержка RU/EN) — берём и контейнер с тултипом, и сам svg-элемент
ICON_SELECTOR = '[data-original-title*="звук" i], [data-original-title*="sound" i], svg.volume_mute_icon'

# Кнопка "Сохранить" определяется по SVG-пути (старый способ, оставляем как один из вариантов)
SAVE_PATH_D = (
    "M14.59 0.59C14.21 0.21 13.7 0 13.17 0H2C0.89 0 0 0.9 0 2V16C0 17.1 0.9 18 2 18H16C17.1 18 18 17.1 18 16V4.83C18 4.3 17.79 3.79 17.41 3.42L14.59 0.59ZM9 16C7.34 16 6 14.66 6 13C6 11.34 7.34 10 9 10C10.66 10 12 11.34 12 13C12 14.66 10.66 16 9 16ZM10 6H4C2.9 6 2 5.1 2 4C2 2.9 2.9 2 4 2H10C11.1 2 12 2.9 12 4C12 5.1 11.1 6 10 6Z"
)
SAVE_BTN = f'button:has(svg path[d="{SAVE_PATH_D}"])'
SAVE_BTN_FALLBACK = f'svg:has(path[d="{SAVE_PATH_D}"])'

# Дополнительные кандидаты для кнопки "Сохранить" (по HTML из карточки)
SAVE_CANDIDATES = [
    SAVE_BTN,
    SAVE_BTN_FALLBACK,
    '[data-original-title="Сохранить"]',
    '[data-original-title="Save"]',
    'svg.diskette_icon',
    'button:has(svg.diskette_icon)',
    'button:has-text("Сохранить")',
    'button:has-text("Save")',
]

# Кнопки закрытия/возврата (RU/EN)
CLOSE_BTN = (
    'button:has-text("Закрыть"), button:has-text("Назад"), '
    'button:has-text("Close"), button:has-text("Back"), '
    '[aria-label="Закрыть"], [title="Закрыть"], [aria-label="Close"], [title="Close"]'
)

# Необязательно: тексты тоста после сохранения (RU/EN)
SUCCESS_HINTS = ['text=Сохранено', 'text=Saved']

# Подтверждение сохранения
CONFIRM_TEXT = 'text=Вы действительно хотите сохранить изменения'
CONFIRM_YES = 'button:has-text("Да"), .fpmmcgp:has-text("Да"), text=Да'

# Тайминги/настройки
CLICK_TIMEOUT = 15000
SCROLL_STEP = 1400
OPEN_WAIT_MS = 300
AFTER_SAVE_WAIT_MS = 200
HOLD_OPEN_SECONDS = int(os.getenv("HOLD_OPEN_SECONDS", "0"))
# Ожидание появления карточек пользователем (навигация вручную)
WAIT_UNTIL_CARDS = os.getenv("WAIT_UNTIL_CARDS", "1") != "0"  # 1 — ждать, 0 — не ждать
POLL_INTERVAL_MS = int(os.getenv("POLL_INTERVAL_MS", "1500"))
MAX_WAIT_SECONDS = int(os.getenv("MAX_WAIT_SECONDS", "0"))  # 0 — без лимита

# Визуальная подсветка кликов
SHOW_CLICKS = os.getenv("SHOW_CLICKS", "1") != "0"
HIGHLIGHT_MS = int(os.getenv("HIGHLIGHT_MS", "500"))
HIGHLIGHT_COLOR = os.getenv("HIGHLIGHT_COLOR", "rgba(0, 200, 255, 0.35)")

GRID_SCROLL_SELECTOR = os.getenv("GRID_SCROLL_SELECTOR", "")

# Предустановленные пресеты (можно расширять)
PRESETS = [
    {
        "name": "Default (env)",
        "url": START_URL,
        "username": os.getenv("SP_USERNAME", ""),
        "password": os.getenv("SP_PASSWORD", ""),
    },
    {
        "name": "Fashion (addrea)",
        "url": "https://fashion.smartplayer.org/#/broadcasts?folderId=33",
        "username": "fashion@addrea.com",
        "password": "7zsF9On1",
    },
    {
        "name": "Neftm Local",
        "url": "http://smartplayer1.neftm.local/cms/#/broadcasts?folderId=1",
        "username": "content_w@addrea.com",
        "password": "1vNpvNX_ob-n",
    },
]

async def highlight_locator(page, locator):
    if not SHOW_CLICKS:
        return
    try:
        box = await locator.bounding_box()
        if not box:
            return
        await page.evaluate(
            "(b,c,ms)=>{const d=document.createElement('div'); d.style.position='fixed'; d.style.left=b.x+'px'; d.style.top=b.y+'px'; d.style.width=b.width+'px'; d.style.height=b.height+'px'; d.style.background=c; d.style.pointerEvents='none'; d.style.zIndex='999999'; d.style.border='2px solid #00c8ff'; d.style.borderRadius='4px'; document.body.appendChild(d); setTimeout(()=>d.remove(), ms); }",
            box,
            HIGHLIGHT_COLOR,
            HIGHLIGHT_MS,
        )
    except Exception:
        pass

# ====== Скорость выполнения (слайдер UI) ======
SPEED_DEFAULT = float(os.getenv("SPEED", "1.0"))  # 1.0 — базовая скорость
SPEED_STATE = {"value": SPEED_DEFAULT}

def TO(ms: int) -> int:
    # Таймауты: уменьшаем при увеличении скорости, но не ниже 500 мс
    factor = SPEED_STATE["value"] if SPEED_STATE["value"] > 0 else 1.0
    return max(500, int(ms / factor))

def AD(ms: int) -> int:
    # Задержки/ожидания: уменьшаем при увеличении скорости, но не ниже 50 мс
    factor = SPEED_STATE["value"] if SPEED_STATE["value"] > 0 else 1.0
    return max(50, int(ms / factor))

def run_speed_slider_ui_main_thread(start_bot_callable) -> None:
    """Запускает Tkinter-слайдер в главном потоке, а бота — в отдельном."""
    try:
        import tkinter as tk

        # Запускаем бота в отдельном потоке (daemon), чтобы окно UI не блокировалось
        Thread(target=start_bot_callable, daemon=True).start()

        root = tk.Tk()
        root.title("Auto Bot Speed")
        tk.Label(root, text="Speed (x)").pack(pady=(8,0))
        scale = tk.Scale(
            root,
            from_=0.2,
            to=3.0,
            orient="horizontal",
            resolution=0.1,
            length=320,
        )
        scale.set(SPEED_STATE["value"])

        def on_change(val: str):
            try:
                SPEED_STATE["value"] = float(val)
            except Exception:
                pass

        scale.configure(command=on_change)
        scale.pack(padx=10, pady=8)
        root.mainloop()
    except Exception:
        # Если не получилось поднять UI (например, без GUI), просто стартуем бота
        start_bot_callable()

async def wait_card_open(page) -> bool:
    """Проверяем, что карточка открылась по любому из признаков."""
    candidates = [*SAVE_CANDIDATES, CLOSE_BTN]
    for css in candidates:
        try:
            await page.locator(css).first.wait_for(timeout=2000)
            return True
        except Exception:
            continue
    return False

async def try_click_sequence(page, loc) -> bool:
    """Пробуем разные варианты клика по элементу. Возвращает True если карточка открылась."""
    await highlight_locator(page, loc)
    try:
        await loc.scroll_into_view_if_needed()
    except Exception:
        pass

    # Набор стратегий клика
    strategies = [
        ("locator.dblclick", lambda: loc.dblclick(timeout=CLICK_TIMEOUT)),
        ("locator.dblclick(force)", lambda: loc.dblclick(timeout=CLICK_TIMEOUT, force=True)),
        ("locator.click x2", lambda: loc.click(timeout=CLICK_TIMEOUT, click_count=2)),
        ("coordinate dblclick center", None),
        ("coordinate dblclick offset1", None),
        ("coordinate dblclick offset2", None),
    ]

    for name, action in strategies:
        try:
            if action is not None:
                await action()
            else:
                # Координатные клики
                box = await loc.bounding_box()
                if not box:
                    continue
                points = [
                    (box["x"] + box["width"]/2, box["y"] + box["height"]/2),
                    (box["x"] + box["width"]/2, box["y"] + box["height"]/2 - 6),
                    (box["x"] + box["width"]/2, box["y"] + box["height"]/2 + 6),
                ]
                for (cx, cy) in points:
                    if SHOW_CLICKS:
                        await page.mouse.move(cx, cy)
                    await page.mouse.click(cx, cy, click_count=2, delay=40)
        except Exception:
            pass

        # Проверяем, что открылось
        if await wait_card_open(page):
            return True
        # Небольшая пауза перед следующей попыткой
        await page.wait_for_timeout(200)

    return False

# Селекторы заголовка карточки (используется для выбора другой карточки по названию)
CARD_TITLE_SELECTOR = os.getenv(
    "CARD_TITLE_SELECTOR",
    ".f1wpuvpe .f1t4bdx7, div.f1wpuvpe div.f1t4bdx7, .v-card__title, div[class*='title' i]",
)

# Контейнер и превью карточки (наиболее кликабельная область)
TILE_CONTAINER_SELECTOR = os.getenv(
    "TILE_CONTAINER_SELECTOR",
    "[id^='broadcast_broadcast_']",
)
TILE_PREVIEW_SELECTOR = os.getenv(
    "TILE_PREVIEW_SELECTOR",
    ".fq9hcom",
)

# Ожидание авторизации (первый запуск), 10 минут
LOGIN_WAIT_TIMEOUT = 10 * 60 * 1000

# Путь к сохранённому состоянию сессии
STATE_PATH = "state.json"

# Настройки авто-логина
AUTO_LOGIN_ENABLED = True
USERNAME_ENV = "SP_USERNAME"
PASSWORD_ENV = "SP_PASSWORD"
FORCE_LOGIN_ENV = "FORCE_LOGIN"

# Возможные селекторы для полей логина/пароля и кнопки входа
DEFAULT_USERNAME_SELECTORS = [
    # Наиболее точные
    'form:has(input[type="password"]) input[type="email"]',
    'form:has(input[type="password"]) input[name="email"]',
    'form:has(input[type="password"]) input[name="username"]',
    'form:has(input[type="password"]) input#email',
    'form:has(input[type="password"]) input[placeholder*="Mail" i]',
    'form:has(input[type="password"]) input[placeholder*="Email" i]',
    'form:has(input[type="password"]) input[placeholder*="почт" i]',
    'form:has(input[type="password"]) input[placeholder*="логин" i]',
    # Фоллбэки шире
    'input[type="email"]',
    'input[name="email"]',
    'input[name="username"]',
    'input#email',
    'input[placeholder*="Mail" i]',
    'input[placeholder*="Email" i]',
    'input[placeholder*="почт" i]',
    'input[placeholder*="логин" i]',
    'form:has(input[type="password"]) input[type="text"]',
]
DEFAULT_PASSWORD_SELECTORS = [
    'form:has(input[type="password"]) input[type="password"]',
    'input[type="password"]',
    'input[name="password"]',
    'input#password',
    'input[placeholder*="парол" i]'
]
DEFAULT_SUBMIT_SELECTORS = [
    'button[type="submit"]',
    'button:has-text("Войти")',
    'button:has-text("Sign in")',
    'button:has-text("Log in")',
]

def resolve_selector_overrides() -> tuple[list[str], list[str], list[str]]:
    # Позволяет переопределять селекторы через переменные окружения
    username_override = os.getenv("SP_USERNAME_SELECTOR")
    password_override = os.getenv("SP_PASSWORD_SELECTOR")
    submit_override = os.getenv("SP_SUBMIT_SELECTOR")

    username_selectors = [username_override] if username_override else DEFAULT_USERNAME_SELECTORS
    password_selectors = [password_override] if password_override else DEFAULT_PASSWORD_SELECTORS
    submit_selectors = [submit_override] if submit_override else DEFAULT_SUBMIT_SELECTORS
    return username_selectors, password_selectors, submit_selectors

async def attempt_auto_login(page) -> bool:
    if not AUTO_LOGIN_ENABLED:
        return False

    username = os.getenv(USERNAME_ENV)
    password = os.getenv(PASSWORD_ENV)
    if not username or not password:
        log("Переменные окружения SP_USERNAME/SP_PASSWORD не заданы — авто‑логин пропущен")
        return False

    log(f"Пробую авто‑логин как: {username}")
    username_selectors, password_selectors, submit_selectors = resolve_selector_overrides()

    # Если уже авторизованы и видим элементы — сразу успех
    try:
        await page.locator(ICON_SELECTOR).first.wait_for(timeout=TO(1000))
        log("Уже авторизованы — элементы страницы найдены")
        return True
    except PWTimeout:
        pass

    # Пытаемся найти поля логина/пароля
    username_locator = None
    password_locator = None

    # 1) Сначала пробуем найти форму логина на текущем экране
    for css in username_selectors:
        locator = page.locator(css).first
        try:
            await locator.wait_for(timeout=TO(2000))
            username_locator = locator
            log(f"Нашёл поле логина по селектору: {css}")
            break
        except PWTimeout:
            continue

    for css in password_selectors:
        locator = page.locator(css).first
        try:
            await locator.wait_for(timeout=TO(2000))
            password_locator = locator
            log(f"Нашёл поле пароля по селектору: {css}")
            break
        except PWTimeout:
            continue

    if not username_locator or not password_locator:
        # 2) Пробуем перейти на явную страницу логина и искать снова
        try:
            parts = urlsplit(page.url)
            login_url = urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, 'login'))
            # Для hash-router (как у SmartPlayer) корректнее заменить hash
            base = parts.scheme + '://' + parts.netloc + (parts.path or '/')
            login_url = base + '#/login'
            await page.goto(login_url, wait_until="load")
            log(f"Перешёл на страницу логина: {login_url}")
            await page.wait_for_load_state("networkidle", timeout=TO(1500))
        except Exception:
            pass

        # Повторный поиск полей
        for css in username_selectors:
            locator = page.locator(css).first
            try:
                await locator.wait_for(timeout=TO(2000))
                username_locator = locator
                log(f"Нашёл поле логина по селектору: {css}")
                break
            except PWTimeout:
                continue

        for css in password_selectors:
            locator = page.locator(css).first
            try:
                await locator.wait_for(timeout=TO(2000))
                password_locator = locator
                log(f"Нашёл поле пароля по селектору: {css}")
                break
            except PWTimeout:
                continue

    if not username_locator or not password_locator:
        log("Не удалось найти поля логина/пароля — авто‑логин не выполнен")
        return False

    # Вводим логин/пароль (fill исключает дублирование)
    await username_locator.fill(username)
    await password_locator.fill(password)

    # Нажимаем кнопку входа
    clicked = False
    for css in submit_selectors:
        try:
            submit_btn = page.locator(css).first
            await submit_btn.wait_for(timeout=1000)
            await submit_btn.click()
            log(f"Нажал кнопку входа: {css}")
            clicked = True
            break
        except PWTimeout:
            continue
        except Exception:
            continue

    if not clicked:
        # Пробуем нажать Enter в поле пароля
        try:
            await password_locator.press("Enter")
            log("Нажал Enter в поле пароля")
        except Exception:
            pass

    # Ждём появления элементов страницы или сетевой тишины
    try:
        await page.locator(ICON_SELECTOR).first.wait_for(timeout=LOGIN_WAIT_TIMEOUT)
        log("Авто‑логин успешен — элементы страницы найдены")
        return True
    except PWTimeout:
        log("Авто‑логин неуспешен — не дождался элементов страницы")
        return False

async def open_save_close(page, icon, index):
    log(f"Открываю карточку #{index+1}")
    open_ok = await try_click_sequence(page, icon)
    if not open_ok:
        raise Exception("Не удалось открыть карточку: не появилась кнопка сохранения")
    await page.wait_for_timeout(AD(OPEN_WAIT_MS))

    # Сохранение
    await save_current_card(page)

async def save_current_card(page) -> None:
    # Ищем рабочую кнопку "Сохранить" из списка кандидатов
    clicked = False
    for css in SAVE_CANDIDATES:
        try:
            btn = page.locator(css).first
            await btn.wait_for(timeout=TO(2000))
            await highlight_locator(page, btn)
            await btn.click()
            log(f"Нажал Сохранить: {css}")
            clicked = True
            break
        except Exception:
            continue
    if not clicked:
        raise Exception("Кнопка Сохранить не найдена")

    # ждём подтверждение (если есть)
    # 1) модалка подтверждения
    try:
        await page.locator(CONFIRM_TEXT).first.wait_for(timeout=TO(1200))
        yes_btn = page.locator(CONFIRM_YES).first
        await highlight_locator(page, yes_btn)
        await yes_btn.click()
        log("Подтвердил сохранение (Да)")
    except Exception:
        pass
    # 2) тосты/хинты
    for hint in SUCCESS_HINTS:
        try:
            await page.locator(hint).first.wait_for(timeout=TO(2500))
            log("Получил подтверждение сохранения")
            break
        except PWTimeout:
            continue
    await page.wait_for_timeout(AD(AFTER_SAVE_WAIT_MS))

    # Не закрываем карточку вручную. Ждём автозакрытие после сохранения
    try:
        await page.locator(SAVE_BTN).first.wait_for(state="detached", timeout=5000)
        log("Карточка закрылась автоматически (кнопка сохранения исчезла)")
    except Exception:
        # как альтернатива — ждём, что список карточек снова виден
        try:
            await page.locator(ICON_SELECTOR).first.wait_for(timeout=5000)
            log("Карточка закрылась автоматически (виден список карточек)")
        except Exception:
            log("Карточка не закрылась автоматически за отведённое время — продолжаю")

async def open_by_title_and_save(page, clickable_locator, index, title_text: str) -> None:
    log(f"Открываю карточку по названию #{index+1}: {title_text}")
    open_ok = await try_click_sequence(page, clickable_locator)
    if not open_ok:
        raise Exception("Не удалось открыть карточку кликами по превью/названию")
    await page.wait_for_timeout(OPEN_WAIT_MS)
    await save_current_card(page)

async def process_all(page):
    processed = 0
    seen_titles: set[str] = set()
    seen_tile_ids: set[str] = set()
    while True:
        count_icons = await page.locator(ICON_SELECTOR).count()
        log(f"На странице найдено карточек (иконок): {count_icons}")
        if count_icons == 0:
            # режим ожидания, пока пользователь не откроет страницу с карточками
            if WAIT_UNTIL_CARDS:
                log("Карточек не видно — жду, пока вы откроете нужный раздел...")
                started = time.time()
                while True:
                    await page.wait_for_timeout(POLL_INTERVAL_MS)
                    count_icons = await page.locator(ICON_SELECTOR).count()
                    if count_icons > 0:
                        log(f"Появились карточки: {count_icons}")
                        break
                    if MAX_WAIT_SECONDS > 0 and (time.time() - started) > MAX_WAIT_SECONDS:
                        log("Истёк лимит ожидания карточек — выхожу")
                        return
                # продолжаем обычную обработку
            else:
                # пробуем проскроллить, вдруг ленивый лоад
                log("Карточек не видно — скроллю для подгрузки")
                await page.mouse.wheel(0, SCROLL_STEP)
                await page.wait_for_timeout(500)
                count_icons = await page.locator(ICON_SELECTOR).count()
                if count_icons == 0:
                    log("Новых карточек не появилось — завершаю обработку")
                    break

        # Идём строго по карточкам-контейнерам, внутри каждого кликаем по иконке звука
        tiles = page.locator(TILE_CONTAINER_SELECTOR)
        tiles_count = await tiles.count()
        log(f"Найдено карточек (контейнеров): {tiles_count}")

        opened_any = False
        for i in range(tiles_count):
            tile = tiles.nth(i)
            # стабильный уникальный id контейнера
            tile_id = None
            try:
                tile_id = await tile.get_attribute("id")
            except Exception:
                tile_id = None
            if tile_id and tile_id in seen_tile_ids:
                continue

            # заголовок карточки для доп. дедупликации
            name = ""
            try:
                name = (await tile.locator(CARD_TITLE_SELECTOR).first.inner_text()).strip()
            except Exception:
                pass
            if name and name.lower() in seen_titles:
                continue

            # иконка звука внутри контейнера
            inner_icon = tile.locator("svg.volume_mute_icon, [data-original-title*='звук' i], [data-original-title*='sound' i]").first
            try:
                await open_save_close(page, inner_icon, processed)
                if tile_id:
                    seen_tile_ids.add(tile_id)
                if name:
                    seen_titles.add(name.lower())
                processed += 1
                opened_any = True
            except Exception as e:
                log(f"❌ Ошибка при обработке контейнера #{i+1}: {e}")
                continue

        if not opened_any:
            # Если ничего не открыли: пытаемся подгрузить и начинаем новый цикл сначала
            await page.mouse.wheel(0, SCROLL_STEP)
            await page.wait_for_timeout(700)
            # Если после скролла новые не появились — считаем, что дошли до конца; начинаем заново
            new_count = await page.locator(ICON_SELECTOR).count()
            if new_count == 0 or new_count == count_icons:
                log("Похоже, конец списка. Начинаю обход заново...")
                # сбрасываем счётчики повторов на новый проход
                processed = 0
                seen_titles.clear()
                seen_tile_ids.clear()
                # Скроллим в самое начало страницы
                try:
                    await page.evaluate("window.scrollTo(0,0)")
                except Exception:
                    pass
                await page.wait_for_timeout(600)
                continue

async def main():
    async with async_playwright() as p:
        log("Запускаю браузер Chromium (видимое окно)")
        browser = await p.chromium.launch(headless=False)  # видно окно
        # Если заданы логин/пароль или включён FORCE_LOGIN — игнорируем сохранённую сессию
        creds_present = bool(os.getenv(USERNAME_ENV)) and bool(os.getenv(PASSWORD_ENV))
        force_login = os.getenv(FORCE_LOGIN_ENV, "0") != "0" or creds_present
        use_saved_state = os.path.exists(STATE_PATH) and not force_login
        context = await browser.new_context(
            storage_state=STATE_PATH if use_saved_state else None
        )
        if use_saved_state:
            log("Использую сохранённую сессию")
        else:
            if force_login:
                log("Игнорирую сохранённую сессию: выполню авто‑логин с указанными данными")
            else:
                log("Сохранённая сессия не найдена")
        page = await context.new_page()
        # Если ожидается логин — сразу идём на #/login, иначе на START_URL
        login_hash_url = None
        if force_login:
            parts = urlsplit(START_URL)
            base = parts.scheme + '://' + parts.netloc + (parts.path or '/')
            login_hash_url = base + '#/login'
        await page.goto(login_hash_url or START_URL, wait_until="load")
        log(f"Открыл страницу: {login_hash_url or START_URL}")

        # Определяем, требуется ли логин (редирект на /login или видим форму логина)
        login_required = force_login
        try:
            await page.wait_for_load_state("networkidle", timeout=TO(1500))
        except Exception:
            pass
        if (not login_required) and ("login" in page.url):
            login_required = True
        else:
            try:
                if await page.locator('input[type="password"]').count() > 0:
                    login_required = True
            except Exception:
                pass

        # Если нет сессии, запрошен принудительный вход или она невалидна — логинимся (сначала авто, затем вручную)
        if (not use_saved_state) or login_required:
            auto_ok = await attempt_auto_login(page)
            if not auto_ok:
                log("Ожидаю ручной вход (до 10 минут)...")
                try:
                    await page.locator('text=Log out, text=Выйти').first.wait_for(timeout=2000)
                except PWTimeout:
                    # Ждём появления целевых элементов на странице трансляций
                    try:
                        await page.locator(ICON_SELECTOR).first.wait_for(timeout=LOGIN_WAIT_TIMEOUT)
                    except PWTimeout:
                        log("Не дождался ручного входа — завершаю")
                        await context.close()
                        await browser.close()
                        return
            # Сохраняем сессию и переходим к стартовой странице
            try:
                await context.storage_state(path=STATE_PATH)
                log(f"Сессия сохранена: {STATE_PATH}")
            except Exception:
                pass
            await page.goto(START_URL, wait_until="load")

        await process_all(page)
        # Задержка перед закрытием окна (по желанию)
        if HOLD_OPEN_SECONDS > 0:
            log(f"Оставляю окно открытым ещё {HOLD_OPEN_SECONDS} сек")
            await page.wait_for_timeout(HOLD_OPEN_SECONDS * 1000)
        log("Закрываю браузер")
        await context.close()
        await browser.close()

def run_control_panel_main_thread() -> None:
    """GUI-панель: URL, логин, пароль, скорость и кнопка Запустить."""
    try:
        import tkinter as tk

        root = tk.Tk()
        root.title("Auto Bot Control")

        # Переменные полей
        preset_names = [p["name"] for p in PRESETS]
        preset_var = tk.StringVar(value=preset_names[0])
        url_var = tk.StringVar(value=START_URL)
        user_var = tk.StringVar(value=os.getenv("SP_USERNAME", ""))
        pwd_var = tk.StringVar(value=os.getenv("SP_PASSWORD", ""))

        def on_preset_change(selection: str):
            preset = next((p for p in PRESETS if p["name"] == selection), PRESETS[0])
            url_var.set(preset.get("url", ""))
            user_var.set(preset.get("username", ""))
            pwd_var.set(preset.get("password", ""))

        # Preset selector
        tk.Label(root, text="Preset").grid(row=0, column=0, sticky="w", padx=8, pady=(8,2))
        preset_menu = tk.OptionMenu(root, preset_var, *preset_names, command=on_preset_change)
        preset_menu.grid(row=0, column=1, sticky="w", padx=8, pady=(8,2))

        # URL
        tk.Label(root, text="Start URL").grid(row=1, column=0, sticky="w", padx=8, pady=(4,2))
        url_entry = tk.Entry(root, width=60, textvariable=url_var)
        url_entry.grid(row=1, column=1, padx=8, pady=(4,2))

        # Username / Password
        tk.Label(root, text="Username").grid(row=2, column=0, sticky="w", padx=8, pady=2)
        user_entry = tk.Entry(root, width=30, textvariable=user_var)
        user_entry.grid(row=2, column=1, sticky="w", padx=8, pady=2)

        tk.Label(root, text="Password").grid(row=3, column=0, sticky="w", padx=8, pady=2)
        # Не скрываем пароль по просьбе пользователя
        pwd_entry = tk.Entry(root, width=30, textvariable=pwd_var)
        pwd_entry.grid(row=3, column=1, sticky="w", padx=8, pady=2)

        # Speed slider
        tk.Label(root, text="Speed (x)").grid(row=4, column=0, sticky="w", padx=8, pady=(8,2))
        speed_var = tk.DoubleVar(value=SPEED_STATE["value"])
        speed_scale = tk.Scale(root, from_=0.2, to=3.0, orient="horizontal", resolution=0.1, length=320,
                               variable=speed_var)
        speed_scale.grid(row=4, column=1, sticky="w", padx=8, pady=(8,2))

        status_var = tk.StringVar(value="Готов к запуску")
        tk.Label(root, textvariable=status_var).grid(row=5, column=0, columnspan=2, sticky="w", padx=8, pady=(4,6))

        def on_start():
            try:
                # Применяем настройки
                SPEED_STATE["value"] = float(speed_var.get() or 1.0)
            except Exception:
                SPEED_STATE["value"] = 1.0
            # Применяем пресет (если выбран)
            selected = next((p for p in PRESETS if p["name"] == preset_var.get()), PRESETS[0])
            # Подставляем значения из пресета, если поля пустые
            if not url_var.get().strip():
                url_var.set(selected["url"]) 
            if not user_var.get().strip():
                user_var.set(selected["username"]) 
            if not pwd_var.get().strip():
                pwd_var.set(selected["password"]) 

            os.environ["SP_USERNAME"] = user_var.get().strip()
            os.environ["SP_PASSWORD"] = pwd_var.get().strip()
            global START_URL
            START_URL = url_var.get().strip() or START_URL
            status_var.set("Запущено… окно можно оставить открытым и менять скорость")
            start_btn.config(state="disabled")

            # Запускаем бота в фоне
            Thread(target=lambda: asyncio.run(main()), daemon=True).start()
            # Подсказка про форс‑логин
            os.environ[FORCE_LOGIN_ENV] = "1" if (os.environ.get("SP_USERNAME") or os.environ.get("SP_PASSWORD")) else "0"

        start_btn = tk.Button(root, text="Запустить", command=on_start)
        start_btn.grid(row=6, column=0, columnspan=2, padx=8, pady=(6,10))

        # Инициализируем поля значениями выбранного пресета
        on_preset_change(preset_var.get())

        root.mainloop()
    except Exception:
        # Фоллбэк без GUI
        asyncio.run(main())

if __name__ == "__main__":
    run_control_panel_main_thread()
