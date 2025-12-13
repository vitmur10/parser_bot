from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from webdriver_manager.chrome import ChromeDriverManager
import time

URL = "https://www.zara.com/ua/uk/%D0%BA%D1%80%D0%BE%D1%81%D1%96%D0%B2%D0%BA%D0%B8-%D0%B7-%D0%BE%D0%B1-%D1%94%D0%BC%D0%BD%D0%BE%D1%8E-%D0%BF%D1%96%D0%B4%D0%BE%D1%88%D0%B2%D0%BE%D1%8E-p12346620.html?v1=452717669&v2=2436823"


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


def accept_cookies(driver):
    try:
        btn = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.ID, "onetrust-accept-btn-handler"))
        )
        btn.click()
        print("✔ Cookie accepted\n")
        time.sleep(1)
    except Exception:
        print("⚠ Cookies banner not found\n")


def handle_geolocation_modal(driver):
    """
    Якщо зʼявляється гео-модалка типу:
    <button data-qa-action="stay-in-store">Так, залишитися на сайті для Poland</button>
    — натискаємо її.
    """
    try:
        btn = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((
                By.XPATH,
                "//button[@data-qa-action='stay-in-store']"
            ))
        )
        btn.click()
        print("✔ Geolocation modal accepted (stay in store)\n")
        time.sleep(1)
    except TimeoutException:
        print("ℹ Geolocation modal not shown\n")
    except Exception as e:
        print(f"⚠ Error while handling geolocation modal: {e}\n")


def main():
    driver = create_driver()
    driver.get(URL)
    time.sleep(5)

    # 1) cookies
    accept_cookies(driver)

    # 2) гео-модалка "Так, залишитися на сайті для Poland"
    handle_geolocation_modal(driver)

    # 3) Шукай кнопку "Додати"
    try:
        add_btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((
                By.XPATH,
                "//div[contains(@class,'product-detail-cart-buttons__main-action')]"
                "//button[@data-qa-action='add-to-cart']"
            ))
        )
        print("🔘 'Додати у кошик' знайдено — клікаю...")
        add_btn.click()
        time.sleep(1)

    except Exception as e:
        print("❗ Кнопку 'Додати' НЕ знайдено — товар може бути недоступний.")
        print("Причина:", e)
        driver.quit()
        return

    # 4) Після кліку читаємо розміри (з popup)
    print("\n📌 Парсимо розміри:")

    try:
        size_buttons = WebDriverWait(driver, 10).until(
            EC.presence_of_all_elements_located((
                By.XPATH, "//ul[@class='size-selector-sizes']/li/button"
            ))
        )

        for btn in size_buttons:
            label = btn.find_element(
                By.XPATH, ".//div[@data-qa-qualifier='size-selector-sizes-size-label']"
            ).text.strip()

            action = btn.get_attribute("data-qa-action")
            if action == "size-in-stock":
                status = "🟢 В наявності"
            elif action == "size-low-on-stock":
                status = "🟡 залишилось декілька одиниць"
            else:
                status = "🔴 Немає"

            print(f"   {label}: {status}")

    except Exception as e:
        print("❗ Розміри не знайдені")
        print("Причина:", e)

    time.sleep(3)
    driver.quit()


if __name__ == "__main__":
    main()
