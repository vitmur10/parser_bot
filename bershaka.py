from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
import time

URL = "https://www.bershka.com/ua/%D0%B4%D0%B6%D0%B8%D0%BD%D1%81%D0%B8-%D0%B1%D0%B0%D1%80%D0%B5%D0%BB-c0p209139014.html?colorId=401"


def create_driver():
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
    return webdriver.Chrome(service=service, options=chrome_options)


def parse_sizes(driver):
    print("\n📌 Розміри:")

    try:
        # чекаємо контейнер розмірів
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located(
                (
                    By.XPATH,
                    "//div[contains(@class,'size-selector-desktop-pdp__sizes')]"
                )
            )
        )
    except Exception as e:
        print("❗ Контейнер розмірів не знайдено:", e)
        return

    # шукаємо всі кнопки-точки розмірів
    size_buttons = driver.find_elements(
        By.XPATH,
        "//div[contains(@class,'size-selector-desktop-pdp__sizes')]"
        "//button[contains(@class,'ui--dot-item')]"
    )

    if not size_buttons:
        print("❗ Кнопки розмірів не знайдені")
        return

    for btn in size_buttons:
        try:
            label_el = btn.find_element(By.CSS_SELECTOR, "span.text__label")
            label = label_el.text.strip()
        except Exception:
            label = "(без назви)"

        classes = btn.get_attribute("class") or ""
        disabled_attr = btn.get_attribute("disabled")
        aria_disabled = btn.get_attribute("aria-disabled") or ""
        aria_desc = (btn.get_attribute("aria-description") or "").strip()

        # логіка визначення статусу
        if disabled_attr is not None or "is-disabled" in classes or aria_disabled == "true" or "Розпродано" in aria_desc:
            status = "🔴 Розпродано"
        else:
            status = "🟢 В наявності"

        print(f"  {label}: {status}")


def main():
    driver = create_driver()
    driver.get(URL)
    time.sleep(5)

    # Назва товару (опційно)
    try:
        name_el = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located(
                (By.XPATH, "//h1[contains(@class,'product-detail-name')]")
            )
        )
        print("Назва товару:", name_el.text.strip())
    except:
        print("Не знайдено назву товару")

    # Кнопка "Додати у кошик"
    try:
        btn = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located(
                (By.XPATH, "//button[@data-qa-anchor='addToCartSizeBtn']")
            )
        )

        print("\nКнопку 'Додати у кошик' знайдено!")

        text = btn.text.strip()
        aria = btn.get_attribute("aria-disabled")
        disabled = btn.get_attribute("disabled")
        classes = btn.get_attribute("class") or ""

        print("Текст кнопки:", repr(text))
        print("aria-disabled:", aria)
        print("disabled:", disabled)
        print("class:", classes)

        if aria == "true" or disabled is not None or "disabled" in classes.lower():
            print("\n❌ ТОВАР / ВИБРАНИЙ РОЗМІР ВІДСУТНІЙ")
        else:
            print("\n✅ ТОВАР / ВИБРАНИЙ РОЗМІР Є В НАЯВНОСТІ")

    except Exception as e:
        print("❗ Кнопку 'Додати у кошик' НЕ знайдено (може бути out of stock / інша верстка)")
        print("Причина:", e)

    # ⬇ Тут одразу парсимо розміри з dot-списку
    parse_sizes(driver)

    time.sleep(5)
    driver.quit()


if __name__ == "__main__":
    main()
