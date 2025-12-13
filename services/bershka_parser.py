import time
import logging
from typing import List, Dict

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException

logger = logging.getLogger(__name__)


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
                (
                    By.XPATH,
                    "//button[@data-qa-anchor='addToCartSizeBtn']"
                )
            )
        )
    except Exception as e:
        logger.warning("⚠ Кнопка 'Додати у кошик' НЕ зʼявилась, парсинг може бути неточним. %s", e)

    # 2️⃣ Чекаємо появи контейнера з розмірами
    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located(
                (
                    By.XPATH,
                    "//div[contains(@class,'size-selector-desktop-pdp__sizes')]"
                )
            )
        )
    except Exception as e:
        return sizes

    # 3️⃣ Знаходимо розміри
    buttons = driver.find_elements(
        By.XPATH,
        "//div[contains(@class,'size-selector-desktop-pdp__sizes')]"
        "//button[contains(@class,'ui--dot-item')]"
    )

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


def check_bershka_one(driver, url: str) -> str:
    """
    Перевіряє один товар Bershka через вже створений driver.
    Повертає ТЕКСТ, з якого потім моніторинг дістає розміри.

    ВАЖЛИВО: для кожного розміру окремий рядок виду:
        🟢 34
        🔴 36
    Це такий самий формат, як у Zara (check_zara),
    і під нього написана extract_available_sizes().
    """
    logger.info("Checking BERSHKA URL: %s", url)

    try:
        driver.get(url)
    except WebDriverException as e:
        logger.warning("❗ Помилка відкриття сторінки %s: %s", url, e)
        return "⚠️ Помилка відкриття сторінки"

    # даємо сторінці прогрузитися
    time.sleep(2)

    # Назва товару (по бажанню — для логів/майбутнього)
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

    # Загальний статус
    if sizes:
        any_available = any(s["available"] for s in sizes)
        if any_available:
            general_status = "📦 Статус: Є в наявності"
        else:
            general_status = "📦 Статус: Немає в наявності"
    else:
        general_status = "📦 Статус: 😕 Не вдалося визначити наявність"

    # ==== Формуємо текст так само, як для Zara ====
    lines: List[str] = []

    # Назву бренду можна не додавати — в заголовку й так є "Товар Bershka"
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
            # КЛЮЧОВИЙ формат: емодзі + пробіл + розмір
            lines.append(f"{mark} {s['size']}")
    else:
        lines.append("📏 Розміри: (не знайдено)")
    result_text = "\n".join(lines)
    logger.info("📤 Фінальний текст статусу:\n%s", result_text)
    logger.info("======================================================")

    return result_text
