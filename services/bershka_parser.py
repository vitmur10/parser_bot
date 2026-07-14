import time
import logging
from typing import List, Dict

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException

logger = logging.getLogger(__name__)


def accept_cookies_bershka(driver):
    """
    Bershka (як і Zara) — сайт Inditex і показує той самий
    OneTrust cookie-banner. Без цього кліку оверлей може
    перекривати/блокувати елементи, і подальші WebDriverWait
    просто тихо валяться в TimeoutException.
    """
    selectors = [
        (By.ID, "onetrust-accept-btn-handler"),
        (By.XPATH, "//button[contains(@id,'accept') or contains(@class,'accept')]"),
    ]
    for by, sel in selectors:
        try:
            btn = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((by, sel))
            )
            btn.click()
            logger.info("✔ Bershka cookies accepted via %s=%s", by, sel)
            time.sleep(1)
            return
        except Exception:
            continue
    logger.info("ℹ Bershka cookies banner not found (можливо, вже прийнятий раніше)")


def parse_sizes(driver) -> List[Dict]:
    """
    Парсить розміри Bershka тоді, коли сторінка повністю прогрузилась.
    Повертає список словників:
      {"size": "M", "available": True/False}
    """
    sizes: List[Dict] = []

    # 1️⃣ Чекаємо кнопку "Додати у кошик"
    try:
        WebDriverWait(driver, 12).until(
            EC.presence_of_element_located(
                (By.XPATH, "//button[@data-qa-anchor='addToCartSizeBtn']")
            )
        )
    except Exception as e:
        logger.warning("⚠ Кнопка 'Додати у кошик' НЕ зʼявилась, парсинг може бути неточним. %s", e)
        # DEBUG: зберігаємо стан сторінки, щоб зрозуміти, що саме побачив бот
        _dump_debug(driver, tag="no_add_to_cart_btn")

    # 2️⃣ Чекаємо появи контейнера з розмірами
    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located(
                (By.XPATH, "//div[contains(@class,'size-selector-desktop-pdp__sizes')]")
            )
        )
    except Exception as e:
        logger.warning("⚠ Контейнер розмірів не з'явився: %s", e)
        _dump_debug(driver, tag="no_size_container")
        return sizes

    # 3️⃣ Знаходимо розміри
    buttons = driver.find_elements(
        By.XPATH,
        "//div[contains(@class,'size-selector-desktop-pdp__sizes')]"
        "//button[contains(@class,'ui--dot-item')]"
    )

    if not buttons:
        logger.warning("⚠ Контейнер розмірів знайдено, але кнопок розмірів 0 — верстка могла змінитись.")
        _dump_debug(driver, tag="empty_size_buttons")

    # 4️⃣ Обробляємо кожен розмір
    for btn in buttons:
        try:
            label_el = btn.find_element(By.CSS_SELECTOR, "span.text__label")
            label = label_el.text.strip()
        except Exception:
            label = "(без назви)"

        classes = btn.get_attribute("class") or ""
        disabled_attr = btn.get_attribute("disabled")
        aria_disabled = (btn.get_attribute("aria-disabled") or "").lower()
        aria_desc = (btn.get_attribute("aria-description") or "").strip()

        unavailable = (
            disabled_attr is not None
            or "is-disabled" in classes
            or aria_disabled == "true"
            or "розпродано" in aria_desc.lower()
        )

        sizes.append({
            "size": label,
            "available": not unavailable,
        })

    return sizes


def _dump_debug(driver, tag: str):
    """
    Зберігає скріншот + HTML сторінки в /tmp, щоб потім подивитись
    що саме бачив Selenium в момент збою (cookie-банер / капча /
    зовсім інша верстка тощо).
    """
    try:
        ts = int(time.time())
        png_path = f"/tmp/bershka_debug_{tag}_{ts}.png"
        html_path = f"/tmp/bershka_debug_{tag}_{ts}.html"
        driver.save_screenshot(png_path)
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        logger.warning("🖼 DEBUG dump saved: %s | %s", png_path, html_path)
        logger.warning("🌐 Current URL at failure: %s", driver.current_url)
    except Exception as dump_err:
        logger.warning("Не вдалося зберегти debug dump: %s", dump_err)


def check_bershka_one(driver, url: str) -> str:
    """
    Перевіряє один товар Bershka через вже створений driver.
    Повертає ТЕКСТ, з якого потім моніторинг дістає розміри.
    """
    logger.info("Checking BERSHKA URL: %s", url)

    try:
        driver.get(url)
    except WebDriverException as e:
        logger.warning("❗ Помилка відкриття сторінки %s: %s", url, e)
        return (
            f"🔗 <a href=\"{url}\">Посилання на товар</a>\n"
            f"⚠️ Помилка відкриття сторінки"
        )

    # даємо сторінці прогрузитися (React рендерить асинхронно —
    # 2 сек часто замало, піднімаємо до 5, як у Zara)
    time.sleep(5)

    # DEBUG: одразу логуємо реальний http-статус і довжину сторінки
    try:
        logger.info(
            "📄 Page loaded. current_url=%s title=%r html_len=%d",
            driver.current_url, driver.title, len(driver.page_source or "")
        )
    except Exception:
        pass

    # ⬇️ ГОЛОВНИЙ ФІКС: приймаємо cookies, як і на Zara
    accept_cookies_bershka(driver)

    # Назва товару
    product_name = ""
    try:
        name_el = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located(
                (
                    By.XPATH,
                    "//h1[contains(@class,'product-detail-info-layout__title') "
                    "or contains(@class,'product-detail-name')]",
                )
            )
        )
        product_name = name_el.text.strip()
    except Exception:
        logger.debug("BERSHKA product name not found")

    # Парсимо розміри
    sizes = parse_sizes(driver)

    # DEBUG: явно бачимо скільки розмірів реально знайшлось
    logger.info("🔎 BERSHKA parsed sizes count=%d raw=%s", len(sizes), sizes)

    # Загальний статус
    if sizes:
        any_available = any(s["available"] for s in sizes)
        general_status = "📦 Статус: Є в наявності" if any_available else "📦 Статус: Немає в наявності"
    else:
        general_status = "📦 Статус: 😕 Не вдалося визначити наявність"

    lines: List[str] = []

    if product_name:
        lines.append(f"🔗 <a href=\"{url}\">{product_name}</a>")
    else:
        lines.append(f"🔗 <a href=\"{url}\">Посилання на товар</a>")
    lines.append(general_status)
    lines.append("")

    if sizes:
        lines.append("📏 Розміри:")
        for s in sizes:
            mark = "🟢" if s["available"] else "🔴"
            lines.append(f"{mark} {s['size']}")
    else:
        lines.append("📏 Розміри: (не знайдено)")

    result_text = "\n".join(lines)
    logger.info("📤 Фінальний текст статусу:\n%s", result_text)
    logger.info("======================================================")

    return result_text