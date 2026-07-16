import logging
import time
import os
import tempfile

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException
from webdriver_manager.chrome import ChromeDriverManager

# ────────────────────────────────────────────────────────────────────────────
#  ЛОГУВАННЯ
# ────────────────────────────────────────────────────────────────────────────
# Standalone-скрипт для ручного дебагу верстки Bershka.
# DEBUG-рівень навмисно ввімкнено, щоб бачити кожен крок взаємодії з DOM.
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("bershaka_debug")

URL = "https://www.bershka.com/ua/%D0%B4%D0%B6%D0%B8%D0%BD%D1%81%D0%B8-%D0%B1%D0%B0%D1%80%D0%B5%D0%BB-c0p209139014.html?colorId=401"


# ────────────────────────────────────────────────────────────────────────────
#  ДЕТЕКТ АНТИБОТ-БЛОКУВАНЬ
# ────────────────────────────────────────────────────────────────────────────
# Тільки специфічні фрази реальних challenge-сторінок (не загальні слова!).
BLOCK_MARKERS = {
    "cloudflare": [
        "cf-browser-verification", "checking your browser before accessing",
        "attention required! | cloudflare", "just a moment...",
        "/cdn-cgi/challenge-platform",
    ],
    "akamai": [
        "errors.edgesuite.net", "reference #18.",
        "access denied  you don't have permission",
    ],
    "access_denied": [
        "access to this page has been denied", "you have been blocked",
        "request unsuccessful. incapsula", "pardon our interruption",
        "please verify you are a human", "доступ заборонено", "429 too many requests",
    ],
}

PRODUCT_MARKERS = (
    "__nuxt__", "size-selector", "addtocartsizebtn", "product-detail", "itxproductpage",
)


def detect_blocking(driver):
    """Повертає опис реального challenge-блокування або None. Викликати ПЕРЕД парсингом.

    Захист від хибних спрацювань: якщо є товарний контент (__NUXT__, size-selector...),
    це нормальна сторінка товару, навіть якщо в JS зустрічається слово 'captcha'.
    """
    logger.debug("→ detect_blocking: перевірка на challenge-сторінки")
    try:
        html = (driver.page_source or "").lower()
        haystack = (driver.title or "").lower() + "\n" + html
    except Exception as e:
        logger.warning("detect_blocking: не вдалося прочитати драйвер: %s", e)
        return None

    if any(pm in html for pm in PRODUCT_MARKERS):
        logger.debug("← detect_blocking: є товарний контент — не блокування")
        return None

    for vendor, markers in BLOCK_MARKERS.items():
        for marker in markers:
            if marker in haystack:
                logger.error(
                    "🚫 БЛОКУВАННЯ [%s] маркер=%r | url=%s | title=%r",
                    vendor, marker, driver.current_url, driver.title,
                )
                return f"{vendor}: '{marker}'"
    logger.debug("← detect_blocking: чисто")
    return None


def _log_page_diagnostics(driver, stage):
    """Логує довжину page_source, URL і наявність базових тегів при таймауті."""
    try:
        html = driver.page_source or ""
        low = html.lower()
        logger.warning(
            "🩺 [%s] html_len=%d url=%s title=%r | <main>=%s <body>=%s <h1>=%s | "
            "size-selector=%s ui--dot-item=%s",
            stage, len(html), driver.current_url, driver.title,
            "<main" in low, "<body" in low, "<h1" in low,
            "size-selector" in low, "ui--dot-item" in low,
        )
        if len(html) < 500:
            logger.warning("🩺 [%s] сторінка майже порожня, html:\n%s", stage, html)
    except Exception as e:
        logger.warning("🩺 [%s] діагностика впала: %s", stage, e)


def create_driver():
    logger.debug("→ create_driver: налаштування Chrome options")
    chrome_options = Options()
    # chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--start-maximized")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("--lang=uk-UA,uk")
    chrome_options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    )
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    logger.debug("← create_driver: driver готовий")
    return driver


def _dump_debug(driver, tag):
    """Скріншот + HTML у /tmp; ПЕРШИМ логує current_url (детект редиректу на капчу)."""
    ts = int(time.time())
    try:
        logger.warning("🌐 [%s] Current URL: %s | title=%r", tag, driver.current_url, driver.title)
    except Exception as e:
        logger.warning("🌐 [%s] не вдалося прочитати current_url: %s", tag, e)
    try:
        dbg_dir = os.path.join(tempfile.gettempdir(), "bershka_debug")
        os.makedirs(dbg_dir, exist_ok=True)
        png_path = os.path.join(dbg_dir, f"bershaka_debug_{tag}_{ts}.png")
        html_path = os.path.join(dbg_dir, f"bershaka_debug_{tag}_{ts}.html")
        driver.save_screenshot(png_path)
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        logger.warning("🖼 [%s] dump saved: %s | %s", tag, png_path, html_path)
    except Exception as e:
        logger.warning("[%s] не вдалося зберегти dump: %s", tag, e)


def parse_sizes(driver):
    logger.info("📌 Розміри:")
    logger.debug("→ parse_sizes: очікую контейнер (.size-selector-desktop-pdp__sizes, 10s)")

    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located(
                (By.XPATH, "//div[contains(@class,'size-selector-desktop-pdp__sizes')]")
            )
        )
        logger.debug("  ✔ контейнер розмірів знайдено")
    except TimeoutException as e:
        logger.warning("❗ TIMEOUT: контейнер розмірів не знайдено за 10с (%s)", e.__class__.__name__)
        _log_page_diagnostics(driver, stage="no_size_container")
        _dump_debug(driver, tag="no_size_container")
        return
    except Exception as e:
        logger.warning("❗ помилка при очікуванні контейнера: %s (%s)", e, type(e).__name__)
        _dump_debug(driver, tag="size_container_error")
        return

    logger.debug("→ шукаю кнопки розмірів (button.ui--dot-item)")
    size_buttons = driver.find_elements(
        By.XPATH,
        "//div[contains(@class,'size-selector-desktop-pdp__sizes')]"
        "//button[contains(@class,'ui--dot-item')]"
    )
    logger.info("  знайдено кнопок розмірів: %d", len(size_buttons))

    if not size_buttons:
        logger.warning("❗ Кнопки розмірів не знайдені — верстка могла змінитись")
        _log_page_diagnostics(driver, stage="empty_size_buttons")
        _dump_debug(driver, tag="empty_size_buttons")
        return

    for idx, btn in enumerate(size_buttons):
        try:
            label_el = btn.find_element(By.CSS_SELECTOR, "span.text__label")
            label = label_el.text.strip()
        except Exception:
            label = "(без назви)"

        classes = btn.get_attribute("class") or ""
        disabled_attr = btn.get_attribute("disabled")
        aria_disabled = btn.get_attribute("aria-disabled") or ""
        aria_desc = (btn.get_attribute("aria-description") or "").strip()

        if disabled_attr is not None or "is-disabled" in classes or aria_disabled == "true" or "Розпродано" in aria_desc:
            status = "🔴 Розпродано"
        else:
            status = "🟢 В наявності"

        logger.info("  [%d] %s: %s", idx, label, status)
        logger.debug(
            "      (disabled=%s aria-disabled=%s classes=%r aria-desc=%r)",
            disabled_attr, aria_disabled, classes, aria_desc,
        )


def main():
    driver = create_driver()

    logger.info("▶ Відкриваю URL: %s", URL)
    logger.debug("→ driver.get")
    try:
        driver.get(URL)
        logger.debug("← driver.get завершено")
    except WebDriverException as e:
        logger.error("❗ WebDriverException при driver.get: %s (%s)", e, type(e).__name__)
        driver.quit()
        return

    logger.debug("→ очікую рендер React (sleep 5s)")
    time.sleep(5)
    logger.info(
        "📄 Page loaded. current_url=%s title=%r html_len=%d",
        driver.current_url, driver.title, len(driver.page_source or ""),
    )

    # Детект блокування ПЕРЕД парсингом
    blocked = detect_blocking(driver)
    if blocked:
        logger.error("🚫 Парсинг перервано — блокування: %s", blocked)
        _dump_debug(driver, tag="antibot_block")
        driver.quit()
        return

    # Назва товару (опційно)
    logger.debug("→ шукаю назву товару (h1.product-detail-name, 10s)")
    try:
        name_el = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located(
                (By.XPATH, "//h1[contains(@class,'product-detail-name')]")
            )
        )
        logger.info("🏷 Назва товару: %r", name_el.text.strip())
    except TimeoutException:
        logger.warning("❗ Назву товару не знайдено за 10с (TimeoutException)")
        _log_page_diagnostics(driver, stage="no_product_name")
    except Exception as e:
        logger.warning("❗ помилка пошуку назви: %s (%s)", e, type(e).__name__)

    # Кнопка "Додати у кошик"
    logger.debug("→ шукаю кнопку 'Додати у кошик' (data-qa-anchor='addToCartSizeBtn', 10s)")
    try:
        btn = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located(
                (By.XPATH, "//button[@data-qa-anchor='addToCartSizeBtn']")
            )
        )
        logger.info("✔ Кнопку 'Додати у кошик' знайдено")

        text = btn.text.strip()
        aria = btn.get_attribute("aria-disabled")
        disabled = btn.get_attribute("disabled")
        classes = btn.get_attribute("class") or ""

        logger.debug(
            "   Текст кнопки=%r aria-disabled=%s disabled=%s class=%r",
            text, aria, disabled, classes,
        )

        if aria == "true" or disabled is not None or "disabled" in classes.lower():
            logger.info("❌ ТОВАР / ВИБРАНИЙ РОЗМІР ВІДСУТНІЙ")
        else:
            logger.info("✅ ТОВАР / ВИБРАНИЙ РОЗМІР Є В НАЯВНОСТІ")

    except TimeoutException as e:
        logger.warning("❗ Кнопку 'Додати у кошик' НЕ знайдено за 10с (TimeoutException)")
        _log_page_diagnostics(driver, stage="no_add_to_cart_btn")
        _dump_debug(driver, tag="no_add_to_cart_btn")
    except Exception as e:
        logger.warning("❗ помилка пошуку кнопки кошика: %s (%s)", e, type(e).__name__)
        _dump_debug(driver, tag="add_to_cart_btn_error")

    # Парсимо розміри з dot-списку
    parse_sizes(driver)

    time.sleep(5)
    logger.debug("→ driver.quit")
    driver.quit()


if __name__ == "__main__":
    main()
