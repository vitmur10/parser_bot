import time
import logging
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import TimeoutException, WebDriverException
from services.user_agents import get_random_ua

logger = logging.getLogger(__name__)


def create_driver(headless: bool = True) -> webdriver.Chrome:
    chrome_options = Options()

    if headless:
        chrome_options.add_argument("--headless=new")

    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")

    ua = get_random_ua()
    chrome_options.add_argument(f"--user-agent={ua}")
    chrome_options.add_argument("--lang=uk-UA,uk")

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    return driver


def safe_get(driver: webdriver.Chrome, url: str, retries: int = 3, timeout: int = 20) -> bool:
    """
    Акуратно відкриває сторінку з кількома спробами.
    """
    for attempt in range(1, retries + 1):
        try:
            driver.set_page_load_timeout(timeout)
            driver.get(url)
            time.sleep(3)
            return True
        except (TimeoutException, WebDriverException) as e:
            logger.warning(f"[safe_get] attempt {attempt}/{retries} failed for {url}: {e}")
            if attempt == retries:
                return False
            time.sleep(2)
    return False
