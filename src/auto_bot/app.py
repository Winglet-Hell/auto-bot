import asyncio
import os
import time
import json
import re
from urllib.parse import urlsplit, urlunsplit
from threading import Thread
from queue import Queue, Empty

# Ленивая поддержка Playwright: не падаем, если пакет не установлен (например, на Python 3.13)
try:  # noqa: SIM105
    from playwright.async_api import async_playwright, TimeoutError as PWTimeout
except Exception:  # ModuleNotFoundError и прочее
    async_playwright = None  # type: ignore[assignment]

    class _DummyTimeoutError(Exception):
        pass

    PWTimeout = _DummyTimeoutError  # type: ignore[assignment]

# Необязательная поддержка анимированного GIF (Pikachu)
try:  # noqa: SIM105
    from PIL import Image, ImageTk  # type: ignore
except Exception:
    Image = None  # type: ignore
    ImageTk = None  # type: ignore

# Простой логгер
def log(message: str) -> None:
    print(f"[bot] {message}", flush=True)
    # Отдельный, более дружелюбный к пользователю текст — для окна статуса
    try:
        friendly = make_user_friendly_log(message)
        # не спамим одинаковыми сообщениями подряд
        if LAST_UI_LOG.get("msg") != friendly:
            LAST_UI_LOG["msg"] = friendly
            LOG_QUEUE.put_nowait(friendly)
    except Exception:
        pass

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
PROFILES_PATH = os.getenv("PROFILES_PATH", "var/profiles.json")
PROFILES_STATE: dict[str, list[dict[str, str]]] = {"profiles": []}
UI_STATE_PATH = os.getenv("UI_STATE_PATH", "var/ui.json")

def get_default_profiles() -> list[dict[str, str]]:
    return [
        {
            "name": "Аккаунт 1",
            "url": START_URL,
            "username": os.getenv("SP_USERNAME", ""),
            "password": os.getenv("SP_PASSWORD", ""),
        },
        {"name": "Аккаунт 2", "url": "", "username": "", "password": ""},
        {"name": "Аккаунт 3", "url": "", "username": "", "password": ""},
    ]

def load_profiles() -> list[dict[str, str]]:
    try:
        with open(PROFILES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            # нормализуем ключи
            result: list[dict[str, str]] = []
            for p in data:
                if not isinstance(p, dict):
                    continue
                result.append(
                    {
                        "name": str(p.get("name", "Профиль")),
                        "url": str(p.get("url", "")),
                        "username": str(p.get("username", "")),
                        "password": str(p.get("password", "")),
                    }
                )
            if result:
                return result
    except Exception:
        pass
    return get_default_profiles()

def save_profiles(profiles: list[dict[str, str]]) -> None:
    try:
        profile_dir = os.path.dirname(PROFILES_PATH)
        if profile_dir:
            os.makedirs(profile_dir, exist_ok=True)
        with open(PROFILES_PATH, "w", encoding="utf-8") as f:
            json.dump(profiles, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    PROFILES_STATE["profiles"] = profiles

def load_ui_state() -> dict:
    try:
        with open(UI_STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}

def save_ui_state(state: dict) -> None:
    try:
        d = os.path.dirname(UI_STATE_PATH)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(UI_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

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
STOP_FLAG = {"stop": False}
BOT_STATE = {"running": False}
ACTIVE_PROFILE_NAME = {"name": "Аккаунт 1"}
RATE_PER_HOUR_AT_1X = int(os.getenv("RATE_PER_HOUR_AT_1X", "120"))  # базовая оценка карточек/час на скорости 1x
LOG_QUEUE: Queue = Queue(maxsize=200)
LAST_UI_LOG: dict[str, str] = {"msg": ""}

def make_user_friendly_log(raw: str) -> str:
    """Преобразует технический лог в короткое понятное сообщение."""
    try:
        text = raw.strip()
        # Частые кейсы
        patterns: list[tuple[str, str]] = [
            ("Запускаю браузер Chromium", "Открываю браузер…"),
            ("Открыл страницу:", "Открыл стартовую страницу"),
            ("Пробую авто‑логин", "Выполняю вход…"),
            ("Уже авторизованы", "Вход выполнен"),
            ("Авто‑логин успешен", "Вход выполнен"),
            ("Авто‑логин неуспешен", "Не удалось войти автоматически"),
            ("Ожидаю ручной вход", "Ожидаю ваш вход…"),
            ("Сессия сохранена", "Сессия сохранена"),
            ("Найдено карточек", "Нашёл карточки на странице"),
            ("Открываю карточку", "Открываю карточку…"),
            ("Нажал Сохранить", "Сохраняю…"),
            ("Получил подтверждение сохранения", "Сохранено"),
            ("Карточка закрылась автоматически", "Карточка закрыта"),
            ("Похоже, конец списка", "Список закончился — начинаю заново"),
            ("Ошибка", "Произошла ошибка — продолжаю"),
        ]
        for needle, friendly in patterns:
            if needle in text:
                return friendly
        # Укорачиваем длинные технические строки
        if len(text) > 100:
            text = text[:97] + "…"
        return text
    except Exception:
        return raw

# ===== Pikachu animation helpers =====
_PIKACHU_FRAMES: list | None = None
_PIKACHU_FRAME_MS: int = 90
_PIKACHU_AFTER: dict = {"id": None, "label": None}
_PIKACHU_ANIMATING: dict[str, bool] = {"on": False}

def _load_pikachu_frames() -> list:
    if Image is None or ImageTk is None:
        return []
    try:
        gif_path = os.getenv("PIKACHU_GIF", os.path.join(os.path.dirname(__file__), "pikachu.gif"))
        if not os.path.exists(gif_path):
            return []
        img = Image.open(gif_path)
        # Determine frame delay and scaling
        base_ms = int(img.info.get("duration", 100))
        try:
            # По умолчанию делаем в 2 раза медленнее базового gif
            default_ms = int(base_ms * 2)
            custom_ms = int(float(os.getenv("PIKACHU_FRAME_MS", str(default_ms))))
        except Exception:
            custom_ms = int(base_ms * 2)
        # Clamp to reasonable range to avoid jerky animation
        global _PIKACHU_FRAME_MS
        _PIKACHU_FRAME_MS = max(120, min(custom_ms, 600))

        try:
            scale = max(1.0, min(5.0, float(os.getenv("PIKACHU_SCALE", "2.5"))))
        except Exception:
            scale = 2.5
        frames: list = []
        try:
            while True:
                frame = img.copy().convert("RGBA")
                new_size = (int(frame.width * scale), int(frame.height * scale))
                frame = frame.resize(new_size, Image.NEAREST)
                frames.append(ImageTk.PhotoImage(frame))
                img.seek(img.tell() + 1)
        except Exception:
            pass
        return frames
    except Exception:
        return []

def start_pikachu_animation(target_label) -> None:
    global _PIKACHU_FRAMES
    if _PIKACHU_FRAMES is None:
        _PIKACHU_FRAMES = _load_pikachu_frames()
    if not _PIKACHU_FRAMES:
        return
    # Если уже анимируем на том же label — ничего не делаем, чтобы не перезапускать на кадр 0
    if _PIKACHU_ANIMATING.get("on") and _PIKACHU_AFTER.get("label") is target_label:
        return
    # Иначе, останавливаем прошлую анимацию (если была) и запускаем новую
    stop_pikachu_animation()
    _PIKACHU_ANIMATING["on"] = True
    _PIKACHU_AFTER["label"] = target_label

    def _loop(idx: int = 0) -> None:
        if not _PIKACHU_ANIMATING["on"]:
            return
        try:
            target_label.configure(image=_PIKACHU_FRAMES[idx % len(_PIKACHU_FRAMES)])
            target_label.image = _PIKACHU_FRAMES[idx % len(_PIKACHU_FRAMES)]  # keep ref
            _PIKACHU_AFTER["id"] = target_label.after(_PIKACHU_FRAME_MS, lambda: _loop(idx + 1))
        except Exception:
            pass

    _loop(0)

def stop_pikachu_animation() -> None:
    try:
        if _PIKACHU_AFTER.get("id") and _PIKACHU_AFTER.get("label") is not None:
            _PIKACHU_AFTER["label"].after_cancel(_PIKACHU_AFTER["id"])  # type: ignore
    except Exception:
        pass
    _PIKACHU_AFTER["id"] = None
    _PIKACHU_AFTER["label"] = None
    _PIKACHU_ANIMATING["on"] = False

def TO(ms: int) -> int:
    # Таймауты: уменьшаем при увеличении скорости, но не ниже 500 мс
    factor = SPEED_STATE["value"] if SPEED_STATE["value"] > 0 else 1.0
    return max(500, int(ms / factor))

def AD(ms: int) -> int:
    # Задержки/ожидания: уменьшаем при увеличении скорости, но не ниже 50 мс
    factor = SPEED_STATE["value"] if SPEED_STATE["value"] > 0 else 1.0
    return max(50, int(ms / factor))

def estimate_rate_per_hour() -> int:
    try:
        base = RATE_PER_HOUR_AT_1X if RATE_PER_HOUR_AT_1X > 0 else 1
        speed = SPEED_STATE["value"] if SPEED_STATE["value"] > 0 else 1.0
        return max(1, int(round(base * speed)))
    except Exception:
        return max(1, RATE_PER_HOUR_AT_1X)

def request_stop() -> None:
    STOP_FLAG["stop"] = True

def run_speed_slider_ui_main_thread(start_bot_callable) -> None:
    """Запускает Tkinter-слайдер в главном потоке, а бота — в отдельном."""
    try:
        # Пытаемся использовать современный скруглённый UI через CustomTkinter
        import customtkinter as ctk  # type: ignore

        # Запускаем бота в отдельном потоке (daemon), чтобы окно UI не блокировалось
        Thread(target=start_bot_callable, daemon=True).start()

        ctk.set_appearance_mode("system")
        ctk.set_default_color_theme("blue")

        root = ctk.CTk()
        root.title("Auto Bot Speed")

        wrapper = ctk.CTkFrame(root, corner_radius=14, padx=14, pady=14)
        wrapper.pack(fill="both", expand=True)

        title = ctk.CTkLabel(wrapper, text="Скорость (x)", font=("", 13, "bold"))
        title.pack(anchor="w", pady=(0, 8))

        value_lbl = ctk.CTkLabel(wrapper, text=f"{SPEED_STATE['value']:.1f}")
        value_lbl.pack(anchor="w", pady=(0, 8))

        scale_var = ctk.DoubleVar(value=SPEED_STATE["value"])  # type: ignore
        scale = ctk.CTkSlider(
            wrapper,
            from_=0.2,
            to=3.0,
            number_of_steps=28,
            corner_radius=10,
            variable=scale_var,
            width=360,
        )
        scale.pack(fill="x")

        def on_change(_val: float | str = 0) -> None:  # noqa: ANN001
            try:
                SPEED_STATE["value"] = float(scale_var.get())
                value_lbl.configure(text=f"{SPEED_STATE['value']:.1f}")
            except Exception:
                pass

        scale.configure(command=on_change)
        root.mainloop()
    except Exception:
        # Фолбэк: обычный Tk/ttk
        try:
            import tkinter as tk
        except Exception:
            start_bot_callable()
        else:
            Thread(target=start_bot_callable, daemon=True).start()

            root = tk.Tk()
            root.title("Auto Bot Speed")
            tk.Label(root, text="Speed (x)").pack(pady=(8, 0))
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

# Путь к сохранённому состоянию сессии (хранится в var/ по умолчанию)
STATE_PATH = os.getenv("STATE_PATH", "var/state.json")

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
        # Пауза: не закрываемся, просто ждём
        while STOP_FLAG["stop"]:
            await page.wait_for_timeout(300)
        count_icons = await page.locator(ICON_SELECTOR).count()
        log(f"На странице найдено карточек (иконок): {count_icons}")
        if count_icons == 0:
            # режим ожидания, пока пользователь не откроет страницу с карточками
            if WAIT_UNTIL_CARDS:
                log("Карточек не видно — жду, пока вы откроете нужный раздел...")
                started = time.time()
                while True:
                    while STOP_FLAG["stop"]:
                        await page.wait_for_timeout(300)
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
            while STOP_FLAG["stop"]:
                await page.wait_for_timeout(300)
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
    # Проверяем, доступен ли Playwright
    if async_playwright is None:
        log("Playwright не установлен — установите зависимости через scripts/run_bot.sh")
        return
    BOT_STATE["running"] = True
    try:
        async with async_playwright() as p:
            log("Запускаю браузер Chromium (видимое окно)")
            browser = await p.chromium.launch(headless=False)
            context = None
            try:
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
                try:
                    await page.goto(login_hash_url or START_URL, wait_until="domcontentloaded", timeout=TO(45000))
                except Exception as e:
                    log(f"Переход на страницу не удался: {e}")
                log(f"Открыл страницу: {login_hash_url or START_URL}")

                # Определяем, требуется ли логин (редирект на /login или видим форму логина)
                login_required = force_login
                try:
                    await page.wait_for_load_state("networkidle", timeout=TO(1500))
                except Exception:
                    pass
                # Пауза до авторизации
                while STOP_FLAG["stop"]:
                    await page.wait_for_timeout(300)
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
                        # Периодически проверяем, чтобы можно было остановить
                        started = time.time()
                        while True:
                            while STOP_FLAG["stop"]:
                                await page.wait_for_timeout(300)
                            try:
                                await page.locator('text=Log out, text=Выйти').first.wait_for(timeout=800)
                                break
                            except Exception:
                                pass
                            try:
                                await page.locator(ICON_SELECTOR).first.wait_for(timeout=800)
                                break
                            except Exception:
                                pass
                            if (time.time() - started) * 1000 > LOGIN_WAIT_TIMEOUT:
                                log("Не дождался ручного входа — завершаю")
                                return
                    # Сохраняем сессию и переходим к стартовой странице
                    try:
                        state_dir = os.path.dirname(STATE_PATH)
                        if state_dir:
                            os.makedirs(state_dir, exist_ok=True)
                        await context.storage_state(path=STATE_PATH)
                        log(f"Сессия сохранена: {STATE_PATH}")
                    except Exception:
                        pass
                    while STOP_FLAG["stop"]:
                        await page.wait_for_timeout(300)
                    try:
                        await page.goto(START_URL, wait_until="domcontentloaded", timeout=TO(45000))
                    except Exception as e:
                        log(f"Переход на стартовую страницу не удался: {e}")

                # Основной цикл
                try:
                    await process_all(page)
                except Exception as e:
                    log(f"Ошибка в процессе обработки: {e}")
            finally:
                # Закрываем браузер, если ещё открыт
                try:
                    if context:
                        await context.close()
                except Exception:
                    pass
                try:
                    await browser.close()
                except Exception:
                    pass
    finally:
        BOT_STATE["running"] = False

def run_control_panel_main_thread() -> None:
    """Графический интерфейс управления. Скруглённый UI на CustomTkinter, с фолбэком на ttk."""
    # Сначала пытаемся запустить скруглённый UI
    try:
        import customtkinter as ctk  # type: ignore
        import tkinter as tk
        # Стили
        ctk.set_appearance_mode("system")
        ctk.set_default_color_theme("blue")

        root = ctk.CTk()
        root.title("Управление Auto Bot")
        try:
            root.iconify(); root.update(); root.deiconify()
        except Exception:
            pass
        # Fixed window size (non-resizable)
        root.geometry("960x560")
        root.minsize(960, 560)
        root.maxsize(960, 560)
        try:
            root.resizable(False, False)
        except Exception:
            pass
        root.grid_columnconfigure(0, weight=0)
        root.grid_columnconfigure(1, weight=1)
        root.grid_rowconfigure(0, weight=1)

        # State
        PROFILES_STATE["profiles"] = load_profiles()
        url_var = tk.StringVar()
        user_var = tk.StringVar()
        pwd_var = tk.StringVar()
        speed_var = tk.DoubleVar(value=SPEED_STATE["value"])  # type: ignore[assignment]
        selected_name = tk.StringVar(value=PROFILES_STATE["profiles"][0]["name"] if PROFILES_STATE["profiles"] else "")

        # Left panel (rounded frame)
        left = ctk.CTkFrame(root, corner_radius=14, fg_color=("#F5F6F8", "#1f1f1f"), width=280)
        left.grid(row=0, column=0, sticky="nsw", padx=(14, 8), pady=14)
        left.grid_propagate(False)
        # rows: title, selector, actions, spacer
        left.grid_rowconfigure(3, weight=1)
        left.grid_columnconfigure(0, weight=1)
        left.grid_columnconfigure(1, weight=0)
        left.grid_columnconfigure(2, weight=0)

        ctk.CTkLabel(left, text="Профили", font=("", 13, "bold")).grid(row=0, column=0, sticky="w", padx=12, pady=(12, 8))

        def profiles_names() -> list[str]:
            return [p["name"] for p in PROFILES_STATE["profiles"]]

        profiles_menu = ctk.CTkOptionMenu(
            left,
            variable=selected_name,
            values=profiles_names() or [""],
            corner_radius=10,
        )
        profiles_menu.grid(row=1, column=0, sticky="ew", padx=(12, 6))
        plus_btn = ctk.CTkButton(left, text="+", width=36, corner_radius=8)
        minus_btn = ctk.CTkButton(left, text="−", width=36, corner_radius=8, fg_color="#ef4444", hover_color="#dc2626")
        plus_btn.grid(row=1, column=1, padx=(0, 6))
        minus_btn.grid(row=1, column=2, padx=(0, 12))

        # Rename remains as a separate full-width action
        rename_btn = ctk.CTkButton(left, text="Переименовать", corner_radius=10)
        rename_btn.grid(row=2, column=0, columnspan=3, sticky="ew", padx=12, pady=10)

        # Pikachu GIF area (left large box)
        pikachu_frame = ctk.CTkFrame(left, corner_radius=12, fg_color=("#ECEFF1", "#1a1a1a"), width=260, height=360)
        pikachu_frame.grid(row=3, column=0, columnspan=3, sticky="nsew", padx=12, pady=(4, 12))
        pikachu_frame.grid_propagate(False)
        pikachu_label = ctk.CTkLabel(pikachu_frame, text="")
        pikachu_label.place(relx=0.5, rely=0.5, anchor="center")
        # Try show first frame immediately or hint
        try:
            global _PIKACHU_FRAMES
            if _PIKACHU_FRAMES is None:
                _PIKACHU_FRAMES = _load_pikachu_frames()
            if _PIKACHU_FRAMES:
                pikachu_label.configure(image=_PIKACHU_FRAMES[0])
                pikachu_label.image = _PIKACHU_FRAMES[0]
            else:
                hint = "Добавьте pikachu.gif в src/auto_bot/\nили укажите PIKACHU_GIF"
                pikachu_label.configure(text=hint, justify="center", text_color="#7a7a7a")
        except Exception:
            pass

        # Right panel (rounded frame)
        # Right panel
        right = ctk.CTkFrame(root, corner_radius=14, fg_color=("#FFFFFF", "#111111"))
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 14), pady=14)
        for i in range(11):
            right.grid_rowconfigure(i, weight=0)
        right.grid_rowconfigure(9, weight=1)
        right.grid_columnconfigure(0, weight=0)
        right.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(right, text="Детали профиля", font=("", 13, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", padx=16, pady=(16, 8))

        ctk.CTkLabel(right, text="Ссылка для входа").grid(row=1, column=0, sticky="w", padx=16, pady=(4, 4))
        url_entry = ctk.CTkEntry(right, textvariable=url_var, corner_radius=10, placeholder_text="https://…")
        url_entry.grid(row=1, column=1, sticky="ew", padx=(0, 16), pady=(4, 4))

        ctk.CTkLabel(right, text="Логин").grid(row=2, column=0, sticky="w", padx=16, pady=4)
        user_entry = ctk.CTkEntry(right, textvariable=user_var, corner_radius=10, placeholder_text="user@mail", width=360)
        user_entry.grid(row=2, column=1, sticky="w", padx=(0, 16), pady=4)

        ctk.CTkLabel(right, text="Пароль").grid(row=3, column=0, sticky="w", padx=16, pady=4)
        pwd_row = ctk.CTkFrame(right, corner_radius=0, fg_color="transparent")
        pwd_row.grid(row=3, column=1, sticky="w", padx=(0, 16), pady=4)
        pwd_entry = ctk.CTkEntry(pwd_row, textvariable=pwd_var, corner_radius=10, show="*", placeholder_text="••••••••", width=360)
        pwd_entry.pack(side="left")
        # Show/Hide password toggle
        def toggle_pwd():
            try:
                pwd_entry.configure(show="" if (pwd_entry.cget("show") == "*") else "*")
                eye_btn.configure(text="🙈" if pwd_entry.cget("show") == "" else "👁")
            except Exception:
                pass
        eye_btn = ctk.CTkButton(pwd_row, text="👁", width=32, corner_radius=10, command=toggle_pwd)
        eye_btn.pack(side="left", padx=(8, 0))

        # Speed (transparent helper frame for lighter look)
        speed_row = ctk.CTkFrame(right, corner_radius=10, fg_color="transparent")
        speed_row.grid(row=4, column=0, columnspan=2, sticky="ew", padx=16, pady=(8, 6))
        speed_row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(speed_row, text="Скорость (x)").grid(row=0, column=0, sticky="w", pady=6)
        speed_scale = ctk.CTkSlider(speed_row, from_=0.2, to=3.0, number_of_steps=28, variable=speed_var, corner_radius=10)
        speed_scale.grid(row=0, column=1, sticky="ew", padx=(12, 12))
        speed_value_lbl = ctk.CTkLabel(speed_row, text=f"{speed_var.get():.1f}")
        speed_value_lbl.grid(row=0, column=2, sticky="e")

        # Estimated throughput row
        rate_row = ctk.CTkFrame(right, corner_radius=10, fg_color="transparent")
        rate_row.grid(row=5, column=0, columnspan=2, sticky="ew", padx=16, pady=(2, 8))
        rate_row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(rate_row, text="Оценка производительности").grid(row=0, column=0, sticky="w")
        rate_value_lbl = ctk.CTkLabel(rate_row, text=f"{estimate_rate_per_hour()} трансляций/час")
        rate_value_lbl.grid(row=0, column=1, sticky="e")

        # Controls
        controls = ctk.CTkFrame(right, corner_radius=10, fg_color="transparent")
        controls.grid(row=7, column=0, columnspan=2, sticky="ew", padx=16, pady=(6, 0))
        controls.grid_columnconfigure(0, weight=1)
        controls.grid_columnconfigure(1, weight=1)
        start_btn = ctk.CTkButton(controls, text="Запустить", corner_radius=10)
        stop_btn = ctk.CTkButton(controls, text="Пауза", corner_radius=10, state="disabled")
        progress = ctk.CTkProgressBar(right)
        progress.grid(row=6, column=0, columnspan=2, sticky="ew", padx=16)
        progress.set(0)
        start_btn.grid(row=0, column=0, padx=(0, 8), pady=8, sticky="ew")
        stop_btn.grid(row=0, column=1, padx=(8, 0), pady=8, sticky="ew")

        # Status
        status_var = tk.StringVar(value="Выберите профиль, отредактируйте параметры и запустите")
        status = ctk.CTkLabel(right, textvariable=status_var, text_color=("#555555", "#aaaaaa"))
        status.grid(row=8, column=0, columnspan=2, sticky="w", padx=16, pady=(10, 6))

        # Large status/log panel (latest log replaces previous)
        log_box = ctk.CTkTextbox(right, height=200, corner_radius=12)
        log_box.grid(row=9, column=0, columnspan=2, sticky="nsew", padx=16, pady=(6, 14))
        log_box.configure(state="disabled")

        # Helpers
        def refresh_profiles_menu(select_name: str | None = None) -> None:
            names = profiles_names()
            profiles_menu.configure(values=names or [""])
            name = select_name or (names[0] if names else "")
            if name:
                selected_name.set(name)

        def load_profile_into_fields(name: str) -> None:
            profiles = PROFILES_STATE["profiles"]
            prof = next((p for p in profiles if p["name"] == name), profiles[0] if profiles else {"url": "", "username": "", "password": ""})
            url_var.set(prof.get("url", ""))
            user_var.set(prof.get("username", ""))
            pwd_var.set(prof.get("password", ""))

        def on_profile_select(_value: str) -> None:
            try:
                name = selected_name.get()
                if not name:
                    return
                load_profile_into_fields(name)
            except Exception:
                pass

        profiles_menu.configure(command=on_profile_select)

        # CRUD
        def on_create_profile() -> None:
            profiles = PROFILES_STATE["profiles"]
            base_name = "Новый профиль"
            i = 1
            existing = {p["name"] for p in profiles}
            name = base_name
            while name in existing:
                i += 1
                name = f"{base_name} {i}"
            new_profile = {
                "name": name,
                "url": url_var.get().strip(),
                "username": user_var.get().strip(),
                "password": pwd_var.get().strip(),
            }
            profiles.append(new_profile)
            save_profiles(profiles)
            refresh_profiles_menu(select_name=name)
            status_var.set("Профиль создан")

        def on_rename_profile() -> None:
            try:
                import tkinter.simpledialog as sd
                old = selected_name.get()
                if not old:
                    return
                new_name = sd.askstring("Переименовать профиль", f"Новое имя для '{old}':", initialvalue=old)
                if not new_name:
                    return
                for p in PROFILES_STATE["profiles"]:
                    if p["name"] == old:
                        p["name"] = new_name
                        break
                save_profiles(PROFILES_STATE["profiles"])
                refresh_profiles_menu(select_name=new_name)
                status_var.set("Профиль переименован")
            except Exception:
                pass

        def on_delete_profile() -> None:
            try:
                import tkinter.messagebox as mb
                name = selected_name.get()
                if not name:
                    return
                if not mb.askyesno("Удалить профиль", f"Удалить профиль '{name}'?"):
                    return
                PROFILES_STATE["profiles"] = [p for p in PROFILES_STATE["profiles"] if p["name"] != name]
                if not PROFILES_STATE["profiles"]:
                    PROFILES_STATE["profiles"] = get_default_profiles()
                save_profiles(PROFILES_STATE["profiles"])
                refresh_profiles_menu(select_name=PROFILES_STATE["profiles"][0]["name"])
                load_profile_into_fields(selected_name.get())
                status_var.set("Профиль удалён")
            except Exception:
                pass

        # Bind actions
        rename_btn.configure(command=on_rename_profile)
        # map plus/minus buttons
        try:
            plus_btn.configure(command=on_create_profile)
            minus_btn.configure(command=on_delete_profile)
        except Exception:
            pass

        # Keyboard shortcuts
        def bind_shortcuts(widget):
            try:
                widget.bind_all("<Command-r>", lambda _e: on_start())
                widget.bind_all("<Control-r>", lambda _e: on_start())
                widget.bind_all("<space>", lambda _e: on_stop())
                widget.bind_all("<Command-s>", lambda _e: save_profiles(PROFILES_STATE["profiles"]))
                widget.bind_all("<Control-s>", lambda _e: save_profiles(PROFILES_STATE["profiles"]))
                widget.bind_all("<Up>", lambda _e: profiles_menu.set(profiles_names()[max(0, profiles_names().index(selected_name.get()) - 1)]) if profiles_names() else None)
                widget.bind_all("<Down>", lambda _e: profiles_menu.set(profiles_names()[min(len(profiles_names()) - 1, profiles_names().index(selected_name.get()) + 1)]) if profiles_names() else None)
            except Exception:
                pass

        bind_shortcuts(root)

        # Start/Pause
        def on_speed_change(_val: float | str = 0) -> None:  # noqa: ANN001
            try:
                SPEED_STATE["value"] = float(speed_var.get() or 1.0)
                speed_value_lbl.configure(text=f"{SPEED_STATE['value']:.1f}")
                rate_value_lbl.configure(text=f"{estimate_rate_per_hour()} трансляций/час")
            except Exception:
                pass
        speed_scale.configure(command=on_speed_change)

        def on_start() -> None:
            try:
                SPEED_STATE["value"] = float(speed_var.get() or 1.0)
            except Exception:
                SPEED_STATE["value"] = 1.0
            STOP_FLAG["stop"] = False
            ACTIVE_PROFILE_NAME["name"] = selected_name.get()
            os.environ["SP_USERNAME"] = user_var.get().strip()
            os.environ["SP_PASSWORD"] = pwd_var.get().strip()
            global START_URL
            START_URL = url_var.get().strip() or START_URL
            status_var.set("Запущено… окно можно оставить открытым и менять скорость")
            start_btn.configure(state="disabled")
            stop_btn.configure(state="normal", text="Пауза")
            try:
                progress.configure(mode="indeterminate")
                progress.start()
            except Exception:
                pass
            # Start Pikachu animation if available
            try:
                start_pikachu_animation(pikachu_label)
            except Exception:
                pass
            os.environ[FORCE_LOGIN_ENV] = "1" if (os.environ.get("SP_USERNAME") or os.environ.get("SP_PASSWORD")) else "0"
            Thread(target=lambda: asyncio.run(main()), daemon=True).start()

        def on_stop() -> None:
            if STOP_FLAG["stop"]:
                STOP_FLAG["stop"] = False
                status_var.set("Продолжаю…")
                stop_btn.configure(text="Пауза")
            else:
                STOP_FLAG["stop"] = True
                status_var.set("Пауза")
                stop_btn.configure(text="Продолжить")
            try:
                if STOP_FLAG["stop"]:
                    stop_pikachu_animation()
                else:
                    start_pikachu_animation(pikachu_label)
            except Exception:
                pass

        start_btn.configure(command=on_start)
        stop_btn.configure(command=on_stop)

        # Initialize (restore UI state)
        saved = load_ui_state()
        # Фиксированный размер не меняем из сохранённого состояния
        refresh_profiles_menu()
        if saved.get("selected_profile"):
            selected_name.set(saved["selected_profile"]) 
        if selected_name.get():
            load_profile_into_fields(selected_name.get())
        did_initial_load = {"done": True}

        def poll() -> None:
            # Buttons state
            if BOT_STATE["running"]:
                status_var.set("Работает… можно менять скорость или нажать Пауза")
                stop_btn.configure(state="normal")
                start_btn.configure(state="disabled")
                try:
                    progress.configure(mode="indeterminate")
                    progress.start()
                except Exception:
                    pass
            else:
                if STOP_FLAG["stop"]:
                    status_var.set("Остановлено")
                else:
                    status_var.set("Готов к запуску")
                start_btn.configure(state="normal")
                stop_btn.configure(state="disabled")
                try:
                    progress.stop()
                    progress.set(0)
                    progress.configure(mode="determinate")
                except Exception:
                    pass
            # Animate pikachu according to state
            try:
                if BOT_STATE["running"] and not STOP_FLAG["stop"]:
                    start_pikachu_animation(pikachu_label)
                else:
                    stop_pikachu_animation()
            except Exception:
                pass
            # Auto-save current fields into selected profile (safe, without wiping non-empty values to empty)
            try:
                name = selected_name.get()
                for p in PROFILES_STATE["profiles"]:
                    if p["name"] == name:
                        current_url = url_var.get().strip()
                        current_user = user_var.get().strip()
                        current_pwd = pwd_var.get().strip()
                        # Не перезаписываем заполненные значения пустыми
                        all_empty = not current_url and not current_user and not current_pwd
                        changed = (
                            current_url != p.get("url", "") or
                            current_user != p.get("username", "") or
                            current_pwd != p.get("password", "")
                        )
                        if did_initial_load.get("done") and changed and (not all_empty or not (p.get("url") or p.get("username") or p.get("password"))):
                            p["url"] = current_url
                            p["username"] = current_user
                            p["password"] = current_pwd
                            save_profiles(PROFILES_STATE["profiles"])
                        break
                # keep rate in sync if something changed speed externally
                rate_value_lbl.configure(text=f"{estimate_rate_per_hour()} трансляций/час")
                # drain log queue and show last line only in the big textbox
                last_msg = None
                try:
                    while True:
                        last_msg = LOG_QUEUE.get_nowait()
                except Empty:
                    pass
                if last_msg is not None:
                    try:
                        log_box.configure(state="normal")
                        log_box.delete("1.0", "end")
                        log_box.insert("end", last_msg)
                        log_box.configure(state="disabled")
                    except Exception:
                        pass
            except Exception:
                pass
            root.after(400, poll)

        poll()

        def persist_ui_state():
            try:
                save_ui_state({
                    "selected_profile": selected_name.get(),
                })
            except Exception:
                pass

        root.protocol("WM_DELETE_WINDOW", lambda: (persist_ui_state(), root.destroy()))
        root.mainloop()
        return
    except ImportError:
        # Фолбэк: старый UI на ttk
        pass

    # Fallback UI (ttk)
    try:
        import tkinter as tk
        from tkinter import ttk

        root = tk.Tk()
        root.title("Управление Auto Bot")
        try:
            root.iconify(); root.update(); root.deiconify()
        except Exception:
            pass
        root.minsize(780, 360)
        root.rowconfigure(0, weight=1)
        root.columnconfigure(0, weight=0)
        root.columnconfigure(1, weight=1)

        try:
            style = ttk.Style()
            for theme in ("aqua", "clam", "default"):
                if theme in style.theme_names():
                    style.theme_use(theme)
                    break
        except Exception:
            pass

        PROFILES_STATE["profiles"] = load_profiles()
        url_var = tk.StringVar()
        user_var = tk.StringVar()
        pwd_var = tk.StringVar()
        speed_var = tk.DoubleVar(value=SPEED_STATE["value"])
        selected_name = tk.StringVar(value=PROFILES_STATE["profiles"][0]["name"] if PROFILES_STATE["profiles"] else "")

        left = ttk.Frame(root, padding=(10, 10, 6, 10))
        left.grid(row=0, column=0, sticky="nsw")
        left.rowconfigure(1, weight=1)
        ttk.Label(left, text="Профили", font=("", 11, "bold")).grid(row=0, column=0, sticky="w")
        profiles_listbox = tk.Listbox(left, height=14, activestyle="dotbox")
        profiles_scroll = ttk.Scrollbar(left, orient="vertical", command=profiles_listbox.yview)
        profiles_listbox.configure(yscrollcommand=profiles_scroll.set)
        profiles_listbox.grid(row=1, column=0, sticky="nsew", pady=(6, 6))
        profiles_scroll.grid(row=1, column=1, sticky="ns", pady=(6, 6), padx=(6, 0))

        btns = ttk.Frame(left)
        btns.grid(row=2, column=0, columnspan=2, sticky="ew")
        create_btn = ttk.Button(btns, text="Создать")
        rename_btn = ttk.Button(btns, text="Переименовать")
        delete_btn = ttk.Button(btns, text="Удалить")
        create_btn.grid(row=0, column=0, padx=(0, 6))
        rename_btn.grid(row=0, column=1, padx=(0, 6))
        delete_btn.grid(row=0, column=2)

        right = ttk.Frame(root, padding=(6, 10, 10, 10))
        right.grid(row=0, column=1, sticky="nsew")
        for i in range(6):
            right.rowconfigure(i, weight=0)
        right.rowconfigure(6, weight=1)
        right.columnconfigure(1, weight=1)

        ttk.Label(right, text="Детали профиля", font=("", 11, "bold")).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(right, text="Ссылка для входа").grid(row=1, column=0, sticky="w", pady=(10, 4))
        url_entry = ttk.Entry(right, textvariable=url_var)
        url_entry.grid(row=1, column=1, sticky="ew", pady=(10, 4))

        ttk.Label(right, text="Логин").grid(row=2, column=0, sticky="w", pady=4)
        user_entry = ttk.Entry(right, textvariable=user_var, width=40)
        user_entry.grid(row=2, column=1, sticky="w", pady=4)

        ttk.Label(right, text="Пароль").grid(row=3, column=0, sticky="w", pady=4)
        pwd_entry = ttk.Entry(right, textvariable=pwd_var, width=40)
        pwd_entry.grid(row=3, column=1, sticky="w", pady=4)

        speed_row = ttk.Frame(right)
        speed_row.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(10, 4))
        ttk.Label(speed_row, text="Скорость (x)").grid(row=0, column=0, sticky="w")
        speed_value_lbl = ttk.Label(speed_row, text=f"{speed_var.get():.1f}")
        speed_value_lbl.grid(row=0, column=1, sticky="w", padx=(6, 10))
        speed_scale = ttk.Scale(speed_row, from_=0.2, to=3.0, orient="horizontal", variable=speed_var, length=360)
        speed_scale.grid(row=0, column=2, sticky="ew")
        speed_row.columnconfigure(2, weight=1)

        controls = ttk.Frame(right)
        controls.grid(row=5, column=0, columnspan=2, sticky="w", pady=(10, 0))
        start_btn = ttk.Button(controls, text="Запустить")
        stop_btn = ttk.Button(controls, text="Пауза", state="disabled")
        start_btn.grid(row=0, column=0, padx=(0, 8))
        stop_btn.grid(row=0, column=1)

        status_var = tk.StringVar(value="Выберите профиль слева, отредактируйте параметры и запустите")
        status = ttk.Label(right, textvariable=status_var, foreground="#555")
        status.grid(row=6, column=0, columnspan=2, sticky="w", pady=(14, 0))

        def refresh_profile_listbox(select_name: str | None = None) -> None:
            profiles_listbox.delete(0, tk.END)
            for p in PROFILES_STATE["profiles"]:
                profiles_listbox.insert(tk.END, p["name"])
            name = select_name or selected_name.get()
            if name:
                for i, p in enumerate(PROFILES_STATE["profiles"]):
                    if p["name"] == name:
                        profiles_listbox.selection_clear(0, tk.END)
                        profiles_listbox.selection_set(i)
                        profiles_listbox.activate(i)
                        profiles_listbox.see(i)
                        selected_name.set(name)
                        break

        def load_profile_into_fields(name: str) -> None:
            profiles = PROFILES_STATE["profiles"]
            prof = next((p for p in profiles if p["name"] == name), profiles[0] if profiles else {"url": "", "username": "", "password": ""})
            url_var.set(prof.get("url", ""))
            user_var.set(prof.get("username", ""))
            pwd_var.set(prof.get("password", ""))

        def on_profile_select(_evt=None):
            try:
                sel = profiles_listbox.curselection()
                if not sel:
                    return
                idx = sel[0]
                name = PROFILES_STATE["profiles"][idx]["name"]
                selected_name.set(name)
                load_profile_into_fields(name)
            except Exception:
                pass

        profiles_listbox.bind("<<ListboxSelect>>", on_profile_select)

        def on_create_profile():
            profiles = PROFILES_STATE["profiles"]
            base_name = "Новый профиль"
            i = 1
            existing = {p["name"] for p in profiles}
            name = base_name
            while name in existing:
                i += 1
                name = f"{base_name} {i}"
            new_profile = {
                "name": name,
                "url": url_var.get().strip(),
                "username": user_var.get().strip(),
                "password": pwd_var.get().strip(),
            }
            profiles.append(new_profile)
            save_profiles(profiles)
            refresh_profile_listbox(select_name=name)
            status_var.set("Профиль создан")

        def on_rename_profile():
            try:
                import tkinter.simpledialog as sd
                old = selected_name.get()
                if not old:
                    return
                new_name = sd.askstring("Переименовать профиль", f"Новое имя для '{old}':", initialvalue=old)
                if not new_name:
                    return
                for p in PROFILES_STATE["profiles"]:
                    if p["name"] == old:
                        p["name"] = new_name
                        break
                save_profiles(PROFILES_STATE["profiles"])
                refresh_profile_listbox(select_name=new_name)
                status_var.set("Профиль переименован")
            except Exception:
                pass

        def on_delete_profile():
            try:
                import tkinter.messagebox as mb
                name = selected_name.get()
                if not name:
                    return
                if not mb.askyesno("Удалить профиль", f"Удалить профиль '{name}'?"):
                    return
                PROFILES_STATE["profiles"] = [p for p in PROFILES_STATE["profiles"] if p["name"] != name]
                if not PROFILES_STATE["profiles"]:
                    PROFILES_STATE["profiles"] = get_default_profiles()
                save_profiles(PROFILES_STATE["profiles"])
                refresh_profile_listbox(select_name=PROFILES_STATE["profiles"][0]["name"])
                load_profile_into_fields(selected_name.get())
                status_var.set("Профиль удалён")
            except Exception:
                pass

        create_btn.configure(command=on_create_profile)
        rename_btn.configure(command=on_rename_profile)
        delete_btn.configure(command=on_delete_profile)

        def on_speed_change(_evt=None):
            try:
                SPEED_STATE["value"] = float(speed_var.get() or 1.0)
                speed_value_lbl.configure(text=f"{SPEED_STATE['value']:.1f}")
            except Exception:
                pass
        speed_scale.configure(command=lambda v: on_speed_change())

        def on_start():
            try:
                SPEED_STATE["value"] = float(speed_var.get() or 1.0)
            except Exception:
                SPEED_STATE["value"] = 1.0
            STOP_FLAG["stop"] = False
            ACTIVE_PROFILE_NAME["name"] = selected_name.get()
            os.environ["SP_USERNAME"] = user_var.get().strip()
            os.environ["SP_PASSWORD"] = pwd_var.get().strip()
            global START_URL
            START_URL = url_var.get().strip() or START_URL
            status_var.set("Запущено… окно можно оставить открытым и менять скорость")
            start_btn.config(state="disabled")
            stop_btn.config(state="normal", text="Пауза")
            os.environ[FORCE_LOGIN_ENV] = "1" if (os.environ.get("SP_USERNAME") or os.environ.get("SP_PASSWORD")) else "0"
            Thread(target=lambda: asyncio.run(main()), daemon=True).start()

        def on_stop():
            if STOP_FLAG["stop"]:
                STOP_FLAG["stop"] = False
                status_var.set("Продолжаю…")
                stop_btn.config(text="Пауза")
            else:
                STOP_FLAG["stop"] = True
                status_var.set("Пауза")
                stop_btn.config(text="Продолжить")

        start_btn.configure(command=on_start)
        stop_btn.configure(command=on_stop)

        refresh_profile_listbox()
        if selected_name.get():
            load_profile_into_fields(selected_name.get())

        def poll():
            if BOT_STATE["running"]:
                status_var.set("Работает… можно менять скорость или нажать Пауза")
                stop_btn.config(state="normal")
                start_btn.config(state="disabled")
            else:
                if STOP_FLAG["stop"]:
                    status_var.set("Остановлено")
                else:
                    status_var.set("Готов к запуску")
                start_btn.config(state="normal")
                stop_btn.config(state="disabled")
            try:
                name = selected_name.get()
                for p in PROFILES_STATE["profiles"]:
                    if p["name"] == name:
                        p["url"] = url_var.get().strip()
                        p["username"] = user_var.get().strip()
                        p["password"] = pwd_var.get().strip()
                        break
                save_profiles(PROFILES_STATE["profiles"])
            except Exception:
                pass
            root.after(400, poll)

        poll()
        root.mainloop()
    except Exception:
        asyncio.run(main())

def run() -> None:
    """Точка входа пакета."""
    run_control_panel_main_thread()


