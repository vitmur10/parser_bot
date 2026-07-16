import logging
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from math import ceil
from typing import Dict, List, Tuple, Callable, Optional
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from config import MAX_PER_BRAND, USER_AGENTS, HEADLESS
from services.bershka_parser import check_bershka_one
from services.zara_parser import check_zara
from utils.urls import detect_brand

logger = logging.getLogger(__name__)


def safe_get(driver, url: str, retries: int = 3, timeout: int = 15):
    """
    Безпечний load сторінки:
    + повторяє відкриття якщо щось пішло не так
    + не дає скрипту впасти
    """

    for attempt in range(1, retries + 1):
        try:
            driver.set_page_load_timeout(timeout)
            driver.get(url)
            time.sleep(3)  # Zara рендерить React → треба дати час
            return True

        except (TimeoutException, WebDriverException) as e:
            print(f"[safe_get] ERROR attempt {attempt}/{retries}: {e}")
            if attempt == retries:
                return False

            # пауза перед повтором
            time.sleep(2)

    return False


def create_driver(headless: bool = False) -> webdriver.Chrome:
    chrome_options = Options()

    if headless:
        chrome_options.add_argument("--headless=new")

    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")

    # Випадковий UA
    ua = random.choice(USER_AGENTS)
    chrome_options.add_argument(f"--user-agent={ua}")

    # мова
    chrome_options.add_argument("--lang=uk-UA,uk")

    chrome_options.add_argument("--window-size=1920,1080")

    #chrome_options.binary_location = "/usr/bin/chromium-browser"
    driver = webdriver.Chrome(options=chrome_options)

    return driver


def _worker_chunk(urls_chunk: List[str], on_result=None) -> List[Tuple[str, str]]:
    """
    Один worker: створює свій driver, проходить по виданих urls, повертає [(url, status), ...]
    """
    if not urls_chunk:
        return []

    driver = None
    results: List[Tuple[str, str]] = []
    try:
        # HEADLESS з config/.env: HEADLESS=0 → видиме вікно Chrome для дебагу переходів.
        driver = create_driver(headless=HEADLESS)
        logger.info("🖥 Driver створено (headless=%s)", HEADLESS)

        for url in urls_chunk:
            brand = detect_brand(url)
            if brand == "zara":
                status = check_zara(driver, url)
            elif brand == "bershka":
                status = check_bershka_one(driver, url)
            else:
                status = "❗ Непідтримуваний домен (не Zara/Bershka)"

            if on_result:
                on_result(url, status)

    except Exception as e:
        logger.exception("Error in worker chunk: %s", e)
        # у випадку фатальної помилки — позначимо всі як помилка
        if not results:
            # якщо ще нічого не встигли записати
            results = [(url, "⚠️ Помилка під час перевірки (worker)") for url in urls_chunk]
    finally:
        if driver is not None:
            driver.quit()

    return results


def check_many_products_selenium_parallel(
        urls: List[str],
        max_workers: int = 4,
        on_result=None,
) -> Dict[str, List[Tuple[str, str]]]:
    """
    Паралельна перевірка через кілька driver'ів.
    max_workers = скільки максимум одночасних браузерів відкривати.

    Якщо переданий on_result(url, status) — буде викликатись одразу після парсингу кожного url.

    Повертає:
    {
      "zara": [(url, status), ...],
      "bershka": [(url, status), ...],
      "other": [(url, status), ...]
    }
    """

    # Спочатку накладемо ліміт по брендах
    zara_urls: List[str] = []
    bershka_urls: List[str] = []
    other_urls: List[str] = []

    for u in urls:
        brand = detect_brand(u)
        if brand == "zara":
            if len(zara_urls) < MAX_PER_BRAND:
                zara_urls.append(u)
        elif brand == "bershka":
            if len(bershka_urls) < MAX_PER_BRAND:
                bershka_urls.append(u)
        else:
            other_urls.append(u)

    # Це ті, що реально будемо ходити Selenium-ом
    to_check: List[str] = zara_urls + bershka_urls

    if not to_check and not other_urls:
        return {"zara": [], "bershka": [], "other": []}

    # Розрахуємо кількість воркерів адекватно до кількості URL
    workers = min(max_workers, max(1, len(to_check)))

    # Розбиваємо на чанки
    chunk_size = ceil(len(to_check) / workers)
    chunks: List[List[str]] = [
        to_check[i: i + chunk_size] for i in range(0, len(to_check), chunk_size)
    ]

    logger.info(
        "Running selenium in parallel: %s urls, %s workers, chunk_size=%s",
        len(to_check), workers, chunk_size
    )

    results: Dict[str, List[Tuple[str, str]]] = {
        "zara": [],
        "bershka": [],
        "other": [],
    }

    # Спочатку додамо "other" як не підтримувані (без Selenium)
    for u in other_urls:
        status = "❗ Непідтримуваний домен (не Zara/Bershka)"
        results["other"].append((u, status))
        if on_result:
            try:
                on_result(u, status)
            except Exception:
                logger.exception("on_result callback failed for url=%s", u)

    # Запускаємо потоки з окремими драйверами
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_chunk = {
            executor.submit(_worker_chunk, chunk, on_result): chunk for chunk in chunks
        }

        for future in as_completed(future_to_chunk):
            chunk_result = future.result()
            for url, status in chunk_result:
                brand = detect_brand(url)
                if brand == "zara":
                    results["zara"].append((url, status))
                elif brand == "bershka":
                    results["bershka"].append((url, status))
                else:
                    results["other"].append((url, status))

    return results


def format_results(results: Dict[str, List[Tuple[str, str]]]) -> List[str]:
    """
    results:
    {
      "zara":    [(url, status), ...],
      "bershka": [(url, status), ...],
      "other":   [(url, status), ...]
    }
    """
    blocks: List[str] = []
    current = ""

    def add_line(line: str):
        nonlocal current, blocks
        if len(current) + len(line) + 1 > 3500:
            if current.strip():
                blocks.append(current)
            current = ""
        current += line + "\n"

    for brand_title, key in [
        ("👗 Zara", "zara"),
        ("🧥 Bershka", "bershka"),
        ("Інше", "other"),
    ]:
        items = results.get(key) or []
        if not items:
            continue

        # Можеш залишити заголовок бренду, або прибрати – як хочеш
        add_line(f"<b>{brand_title}</b>\n")

        for _, status in items:
            # status вже містить красивий блок з лінком + розмірами
            add_line(status)
            add_line("")  # відступ між товарами

        add_line("")  # відступ між брендами

    if current.strip():
        blocks.append(current)

    return blocks


def check_urls_for_user(urls: List[str]) -> Dict[str, str]:
    """
    Перевіряє список URL одного юзера.
    Повертає dict {url: status_text}.

    Тепер всередині використовує паралельний Selenium
    (до 4 окремих драйверів через ThreadPoolExecutor).
    """
    # Використовуємо вже готову паралельну функцію
    parallel_results = check_many_products_selenium_parallel(
        urls,
        max_workers=4,   # 👈 4 драйвери одночасно
    )

    status_map: Dict[str, str] = {}

    # Розкладаємо результати по плоскому dict {url: status}
    for brand_key in ("zara", "bershka", "other"):
        for url, status in parallel_results.get(brand_key, []):
            status_map[url] = status

    # На всякий випадок — якщо якийсь URL не потрапив у результати
    for u in urls:
        if u not in status_map:
            status_map[u] = "⚠️ Не вдалося перевірити URL"

    return status_map



def check_urls_for_user_parallel(
    urls: List[str],
    max_workers: int = 4,
    on_result: Optional[Callable[[str, str], None]] = None,
) -> Dict[str, str]:
    """
    Паралельна версія для моніторингу:
    - всередині використовує check_many_products_selenium_parallel(...)
    - повертає {url: status_text}
    - якщо переданий on_result(url, status) — викликається одразу по мірі готовності кожного url
    """
    grouped = check_many_products_selenium_parallel(
        urls=urls,
        max_workers=max_workers,
        on_result=on_result,   # ✅ прокидаємо callback далі
    )

    status_map: Dict[str, str] = {}

    for _, items in grouped.items():
        for url, status in items:
            status_map[url] = status

    return status_map

