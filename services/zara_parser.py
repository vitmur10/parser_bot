import logging
import time

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException  # ⬅ додали

logger = logging.getLogger(__name__)


def accept_cookies(driver):
    """
    Приймаємо кукі, якщо банер є.
    """
    try:
        btn = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.ID, "onetrust-accept-btn-handler"))
        )
        btn.click()
        logger.info("✔ Cookies accepted")
        time.sleep(1)
    except Exception:
        logger.info("⚠ Cookies banner not found")


def handle_geolocation_modal(driver):
    """
    Закриваємо гео-модалку, якщо з'явилась:
    кнопка:
    <button class="zds-button geolocation-modal__button ..."
            data-qa-action="stay-in-store">
        Так, залишитися на сайті для Poland
    </button>
    """
    try:
        btn = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((
                By.XPATH,
                "//button[@data-qa-action='stay-in-store']"
            ))
        )
        btn.click()
        logger.info("✔ Geolocation modal accepted (stay in store)")
        time.sleep(1)
    except TimeoutException:
        logger.info("ℹ Geolocation modal not shown")
    except Exception as e:
        logger.info("⚠ Error while handling geolocation modal: %s", e)


def check_zara(driver, url: str) -> str:
    """
    Перевірка одного товару Zara.
    Використовує ВЖЕ СТВОРЕНИЙ driver (ми його не створюємо і не закриваємо тут).

    Повертає текстовий статус (рядок), який потім йде в бот.
    Формат розмірів спеціально зроблений під extract_available_sizes:
    кожен доступний розмір має рядок виду "🟢 32", недоступний – "🔴 34".
    """
    logger.info("Checking ZARA URL: %s", url)

    try:
        driver.get(url)
    except Exception as e:
        logger.warning("❗ Помилка відкриття сторінки %s: %s", url, e)
        return (
            f"<b>🧵 Zara</b>\n"
            f"🔗 <a href=\"{url}\">Посилання на товар</a>\n"
            f"⚠️ Помилка відкриття сторінки"
        )

    # даємо React-у прогрузитися
    time.sleep(5)

    # 1) кукі
    accept_cookies(driver)

    # 2) гео-модалка "Так, залишитися на сайті для Poland"
    handle_geolocation_modal(driver)

    # 3) назва товару
    product_name = ""
    try:
        name_el = WebDriverWait(driver, 7).until(
            EC.presence_of_element_located((
                By.XPATH,
                "//h1[contains(@class,'product-detail-info__header-name')]"
            ))
        )
        product_name = name_el.text.strip()
        logger.info("ZARA product name: %s", product_name)
    except Exception:
        logger.info("ZARA product name not found")

    # 4) шукаємо кнопку "Додати у кошик" і клікаємо, щоб відкрився попап розмірів
    try:
        add_btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((
                By.XPATH,
                "//div[contains(@class,'product-detail-cart-buttons__main-action')]"
                "//button[@data-qa-action='add-to-cart']"
            ))
        )
        logger.info("🔘 'Додати у кошик' знайдено, клікаю...")
        add_btn.click()
        time.sleep(1)
    except Exception as e:
        logger.info("❗ Кнопку 'Додати' НЕ знайдено: %s", e)
        header_lines = ["<b>🧵 Zara</b>"]
        if product_name:
            header_lines.append(product_name)
        header_lines.append(f"🔗 <a href=\"{url}\">Посилання на товар</a>")
        header_lines.append("❌ Кнопку 'Додати у кошик' не знайдено — товар/розміри можуть бути недоступні.")
        return "\n".join(header_lines)

    # 5) парсимо розміри з попапу
    lines: list[str] = ["<b>🧵 Zara</b>"]

    # бренд + назва
    if product_name:
        lines.append(product_name)

    # посилання
    lines.append(f"🔗 <a href=\"{url}\">{product_name}</a>")

    # розміри
    lines.append("📏 Розміри:")

    try:
        size_buttons = WebDriverWait(driver, 10).until(
            EC.presence_of_all_elements_located((
                By.XPATH,
                "//ul[@class='size-selector-sizes']/li/button"
            ))
        )

        for btn in size_buttons:
            try:
                label_el = btn.find_element(
                    By.XPATH,
                    ".//div[@data-qa-qualifier='size-selector-sizes-size-label']"
                )
                label = label_el.text.strip()
            except Exception:
                continue

            action = (btn.get_attribute("data-qa-action") or "").lower()
            # data-qa-action: size-in-stock / size-out-of-stock
            if action == "size-in-stock":
                mark = "🟢"
            elif action == "size-low-on-stock":
                mark = "🟡"
            else:
                mark = "🔴"

            # формат важливий для extract_available_sizes
            lines.append(f"{mark} {label}")

    except Exception as e:
        logger.info("❗ Розміри не знайдені: %s", e)
        lines.append("❗ Розміри не знайдені")

    return "\n".join(lines)
