import time
import logging
import tempfile
import os
from typing import List, Dict, Optional

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────────────
#  ДЕТЕКТ АНТИБОТ-БЛОКУВАНЬ
# ────────────────────────────────────────────────────────────────────────────
# Маркери РЕАЛЬНИХ challenge-сторінок антибот-систем.
# ВАЖЛИВО: тільки специфічні фрази, яких НЕ буває у звичайному коді сайту.
# Загальні слова (captcha, recaptcha, akamai, ray id) НЕ використовуємо —
# вони зустрічаються в CSP-конфізі, скриптах аналітики й нормальному JS Bershka,
# що давало хибні спрацювання (див. розбір логів).
BLOCK_MARKERS = {
    "cloudflare": [
        "cf-browser-verification",
        "checking your browser before accessing",
        "attention required! | cloudflare",
        "just a moment...",
        "/cdn-cgi/challenge-platform",
    ],
    "akamai": [
        "errors.edgesuite.net",
        "reference #18.",  # типовий Akamai reference-код
        "access denied  you don't have permission",
    ],
    "access_denied": [
        "access to this page has been denied",
        "you have been blocked",
        "request unsuccessful. incapsula",
        "pardon our interruption",  # PerimeterX
        "please verify you are a human",
        "доступ заборонено",
        "429 too many requests",
    ],
}

# Товарні маркери: якщо сторінка їх містить — це РЕАЛЬНА сторінка товару,
# і жодне "блокування" не рахуємо (захист від хибних спрацювань).
PRODUCT_MARKERS = (
    "__nuxt__",
    "size-selector",
    "addtocartsizebtn",
    "product-detail",
    "itxproductpage",
)


def detect_blocking(driver) -> Optional[str]:
    """
    Перевіряє поточну сторінку на РЕАЛЬНІ challenge-сторінки антибот-систем
    (Cloudflare / Akamai / Access Denied / PerimeterX / Incapsula).

    Повертає рядок з описом виявленого блокування або None, якщо все чисто.
    Викликати ОДРАЗУ після завантаження сторінки, ПЕРЕД парсингом.

    Захист від хибних спрацювань: якщо на сторінці присутній товарний контент
    (__NUXT__, size-selector, product-detail тощо) — це нормальна сторінка товару,
    навіть якщо в її JS зустрічається слово 'captcha'.
    """
    logger.debug("→ detect_blocking: старт перевірки на антибот challenge-сторінки")
    try:
        html = (driver.page_source or "").lower()
        title = (driver.title or "").lower()
        current_url = driver.current_url
    except Exception as e:
        logger.warning("detect_blocking: не вдалося прочитати стан драйвера: %s", e)
        return None

    # 1. Якщо є товарний контент — це точно НЕ challenge-сторінка.
    has_product = any(pm in html for pm in PRODUCT_MARKERS)
    if has_product:
        logger.debug(
            "← detect_blocking: знайдено товарний контент (has_product=True) — не блокування"
        )
        return None

    # 2. Товарного контенту немає — шукаємо challenge-маркери.
    haystack = title + "\n" + html
    for vendor, markers in BLOCK_MARKERS.items():
        for marker in markers:
            if marker in haystack:
                logger.error(
                    "🚫 БЛОКУВАННЯ ВИЯВЛЕНО [%s] маркер=%r | url=%s | title=%r | html_len=%d",
                    vendor, marker, current_url, driver.title, len(html),
                )
                return f"{vendor}: '{marker}'"

    # 3. Немає ні товару, ні challenge-маркерів, але сторінка коротка — підозріло.
    if len(html) < 5000:
        logger.warning(
            "⚠ detect_blocking: немає товарного контенту, сторінка коротка "
            "(html_len=%d) — можливий редирект/заглушка. url=%s title=%r",
            len(html), current_url, driver.title,
        )

    logger.debug("← detect_blocking: явних маркерів блокування не знайдено")
    return None


# ────────────────────────────────────────────────────────────────────────────
#  ДІАГНОСТИКА page_source
# ────────────────────────────────────────────────────────────────────────────
def _log_page_diagnostics(driver, stage: str):
    """
    Логує ключові метрики сторінки: довжину page_source, поточний URL,
    наявність базових тегів (<main>, <body>, <h1>) та товарних маркерів.
    Викликається при TimeoutException, щоб зрозуміти, ЩО саме бачив бот.
    """
    try:
        html = driver.page_source or ""
        low = html.lower()
        logger.warning(
            "🩺 ДІАГНОСТИКА [%s]: html_len=%d | url=%s | title=%r",
            stage, len(html), driver.current_url, driver.title,
        )
        logger.warning(
            "🩺 [%s] теги: <main>=%s <body>=%s <h1>=%s | товарні: add-to-cart=%s "
            "size-selector=%s ui--dot-item=%s",
            stage,
            "<main" in low,
            "<body" in low,
            "<h1" in low,
            "add-to-cart" in low or "addtocart" in low,
            "size-selector" in low,
            "ui--dot-item" in low,
        )
        if len(html) < 500:
            # зовсім порожня/обрізана сторінка — виводимо весь вміст
            logger.warning("🩺 [%s] СТОРІНКА МАЙЖЕ ПОРОЖНЯ, повний html:\n%s", stage, html)
    except Exception as e:
        logger.warning("🩺 [%s] не вдалося зібрати діагностику page_source: %s", stage, e)


def accept_cookies_bershka(driver):
    """
    Bershka (як і Zara) — сайт Inditex і показує той самий
    OneTrust cookie-banner. Без цього кліку оверлей може
    перекривати/блокувати елементи, і подальші WebDriverWait
    просто тихо валяться в TimeoutException.
    """
    logger.debug("→ accept_cookies_bershka: старт пошуку cookie-банера")
    selectors = [
        (By.ID, "onetrust-accept-btn-handler"),
        (By.CSS_SELECTOR, "button.onetrust-close-btn-handler"),
        (By.CSS_SELECTOR, "#onetrust-close-btn-container button"),
        (By.XPATH, "//button[contains(@id,'accept') or contains(@class,'accept')]"),
    ]
    for by, sel in selectors:
        logger.debug("  · пробую cookie-селектор %s=%s", by, sel)
        try:
            btn = WebDriverWait(driver, 2).until(
                EC.element_to_be_clickable((by, sel))
            )
            btn.click()
            logger.info("✔ Bershka cookies accepted via %s=%s", by, sel)
            time.sleep(1)
            logger.debug("← accept_cookies_bershka: банер прийнято")
            return
        except Exception:
            logger.debug("  · cookie-селектор %s=%s не спрацював", by, sel)
            continue
    logger.info("ℹ Bershka cookies banner not found (можливо, вже прийнятий раніше)")
    logger.debug("← accept_cookies_bershka: банер не знайдено")


# Кандидати-селектори кнопок розмірів (верстка Bershka періодично змінюється).
# Пробуємо по черзі; перший, що дає >0 елементів — використовуємо.
SIZE_BUTTON_SELECTORS = [
    # старий варіант
    "div[class*='size-selector-desktop-pdp__sizes'] button[class*='ui--dot-item']",
    # dot-item без прив'язки до контейнера
    "button[class*='dot-item']",
    "li[class*='dot-item']",
    # за data-qa-anchor (Bershka часто мітить розміри)
    "[data-qa-anchor='sizeListItem']",
    "[data-qa-anchor*='size']",
    # загальні варіанти контейнера розмірів
    "div[class*='size-selector'] button",
    "div[class*='size-selector'] li",
    "ul[class*='sizes'] li",
    "[class*='size-list'] [class*='size']",
]


def _probe_size_selectors(driver, tag: str = "probe") -> None:
    """
    Діагностичний зонд: через JS перебирає багато кандидатів-селекторів
    і логує, які саме елементи/класи реально є в зоні розмірів.
    Ціль — знайти актуальний селектор кнопок розмірів після зміни верстки.
    """
    logger.warning("🔬 [%s] ЗОНД СЕЛЕКТОРІВ РОЗМІРІВ: старт", tag)
    js = r"""
    function summarize(nodes, limit) {
        var out = [];
        for (var i = 0; i < nodes.length && i < limit; i++) {
            var n = nodes[i];
            out.push({
                tag: n.tagName,
                cls: (n.getAttribute('class') || '').slice(0, 120),
                qa: n.getAttribute('data-qa-anchor'),
                txt: (n.textContent || '').trim().slice(0, 30),
                disabled: n.getAttribute('disabled'),
                ariaDisabled: n.getAttribute('aria-disabled')
            });
        }
        return out;
    }
    var result = {};
    // 1. будь-які елементи з класом, що містить 'size'
    var sizeish = document.querySelectorAll("[class*='size']");
    result.sizeish_count = sizeish.length;
    result.sizeish_sample = summarize(sizeish, 12);
    // 2. будь-які елементи з data-qa-anchor, що містить 'size'
    var qaSize = document.querySelectorAll("[data-qa-anchor*='size'], [data-qa-anchor*='Size']");
    result.qa_size_count = qaSize.length;
    result.qa_size_sample = summarize(qaSize, 12);
    // 3. контейнер size-selector: outerHTML (обрізаний)
    var cont = document.querySelector("[class*='size-selector']");
    result.container_found = !!cont;
    result.container_html = cont ? cont.outerHTML.slice(0, 1200) : null;
    return result;
    """
    try:
        res = driver.execute_script(js) or {}
    except Exception as e:
        logger.warning("🔬 [%s] зонд не зміг виконати JS: %s", tag, e)
        return

    logger.warning("🔬 [%s] elements[class*=size]: %s", tag, res.get("sizeish_count"))
    for i, el in enumerate(res.get("sizeish_sample") or []):
        logger.warning(
            "🔬 [%s]   size#%d <%s> class=%r qa=%r disabled=%r aria=%r txt=%r",
            tag, i, el.get("tag"), el.get("cls"), el.get("qa"),
            el.get("disabled"), el.get("ariaDisabled"), el.get("txt"),
        )
    logger.warning("🔬 [%s] elements[data-qa-anchor*=size]: %s", tag, res.get("qa_size_count"))
    for i, el in enumerate(res.get("qa_size_sample") or []):
        logger.warning(
            "🔬 [%s]   qa#%d <%s> class=%r qa=%r txt=%r",
            tag, i, el.get("tag"), el.get("cls"), el.get("qa"), el.get("txt"),
        )
    logger.warning("🔬 [%s] container[class*=size-selector] found=%s", tag, res.get("container_found"))
    if res.get("container_html"):
        logger.warning("🔬 [%s] container outerHTML (1200 chars):\n%s", tag, res["container_html"])

    # Перебір CSS-кандидатів через Selenium — скільки кожен дає
    for sel in SIZE_BUTTON_SELECTORS:
        try:
            n = len(driver.find_elements(By.CSS_SELECTOR, sel))
        except Exception as e:
            n = f"ERR:{e.__class__.__name__}"
        logger.warning("🔬 [%s] CSS %-60s → %s", tag, sel, n)
    logger.warning("🔬 [%s] ЗОНД СЕЛЕКТОРІВ: кінець", tag)


def _find_size_buttons(driver):
    """
    Пробує кілька селекторів по черзі. Повертає (buttons, selector_used).
    """
    for sel in SIZE_BUTTON_SELECTORS:
        try:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
        except Exception:
            els = []
        if els:
            logger.info("  [3/4] кнопки розмірів знайдено селектором: %s (%d шт.)", sel, len(els))
            return els, sel
    return [], None


def parse_sizes(driver) -> List[Dict]:
    """
    Парсить розміри Bershka тоді, коли сторінка повністю прогрузилась.
    Повертає список словників:
      {"size": "M", "available": True/False}
    """
    sizes: List[Dict] = []
    logger.debug("→ parse_sizes: старт")

    # 1️⃣ Чекаємо кнопку "Додати у кошик"
    logger.debug("  [1/4] очікую кнопку 'Додати у кошик' (data-qa-anchor='addToCartSizeBtn', timeout=12s)")
    try:
        WebDriverWait(driver, 12).until(
            EC.presence_of_element_located(
                (By.XPATH, "//button[@data-qa-anchor='addToCartSizeBtn']")
            )
        )
        logger.debug("  [1/4] ✔ кнопку 'Додати у кошик' знайдено")
    except TimeoutException as e:
        logger.warning("⚠ [1/4] TIMEOUT: кнопка 'Додати у кошик' НЕ зʼявилась за 12с. %s", e.__class__.__name__)
        _log_page_diagnostics(driver, stage="no_add_to_cart_btn")
        _dump_debug(driver, tag="no_add_to_cart_btn")
    except Exception as e:
        logger.warning("⚠ [1/4] неочікувана помилка при очікуванні кнопки: %s (%s)", e, type(e).__name__)
        _dump_debug(driver, tag="add_to_cart_btn_error")

    # 2️⃣ Чекаємо появи БУДЬ-ЯКОГО контейнера розмірів (клас містить 'size-selector').
    #    Раніше жорстко чекали 'size-selector-desktop-pdp__sizes' — але верстка
    #    змінюється, тому послаблюємо умову й не робимо це блокуючим фактором.
    logger.debug("  [2/4] очікую контейнер розмірів (contains(@class,'size-selector'), timeout=10s)")
    container_found = False
    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "[class*='size-selector']")
            )
        )
        container_found = True
        logger.debug("  [2/4] ✔ контейнер розмірів знайдено")
    except TimeoutException:
        logger.warning("⚠ [2/4] TIMEOUT: контейнер [class*='size-selector'] не з'явився за 10с.")
        _log_page_diagnostics(driver, stage="no_size_container")
    except Exception as e:
        logger.warning("⚠ [2/4] неочікувана помилка при очікуванні контейнера: %s (%s)", e, type(e).__name__)

    # 3️⃣ Знаходимо кнопки розмірів через перебір кількох селекторів.
    logger.debug("  [3/4] шукаю кнопки розмірів (перебір SIZE_BUTTON_SELECTORS)")
    buttons, used_selector = _find_size_buttons(driver)
    logger.info("  [3/4] знайдено кнопок розмірів: %d (селектор=%s)", len(buttons), used_selector)

    if not buttons:
        logger.warning(
            "⚠ [3/4] Кнопок розмірів 0 (container_found=%s) — верстка змінилась. "
            "Запускаю зонд селекторів.", container_found,
        )
        _probe_size_selectors(driver, tag="empty_size_buttons")
        _dump_debug(driver, tag="empty_size_buttons")
        logger.debug("← parse_sizes: вихід без розмірів (кнопок не знайдено)")
        return sizes

    # 4️⃣ Обробляємо кожен розмір
    logger.debug("  [4/4] обробляю %d кнопок розмірів", len(buttons))
    for idx, btn in enumerate(buttons):
        # Витяг назви розміру з кількома fallback-ами.
        label = ""
        for label_sel in ("span.text__label", "[class*='label']", "span"):
            try:
                el = btn.find_element(By.CSS_SELECTOR, label_sel)
                label = (el.text or "").strip()
                if label:
                    break
            except Exception:
                continue
        if not label:
            label = (btn.text or "").strip()
        if not label:
            label = (btn.get_attribute("aria-label") or "").strip()

        classes = btn.get_attribute("class") or ""
        disabled_attr = btn.get_attribute("disabled")
        aria_disabled = (btn.get_attribute("aria-disabled") or "").lower()
        aria_desc = (btn.get_attribute("aria-description") or "").strip()
        data_qa = btn.get_attribute("data-qa-anchor") or ""
        try:
            outer = (btn.get_attribute("outerHTML") or "")[:160]
        except Exception:
            outer = ""

        # ДЕТАЛЬНИЙ лог кожної кнопки — щоб точно бачити маркери доступності/недоступності.
        logger.warning(
            "🔎 btn[%d] label=%r class=%r disabled=%r aria-disabled=%r qa=%r desc=%r\n       outer=%s",
            idx, label, classes, disabled_attr, aria_disabled, data_qa, aria_desc, outer,
        )

        # Пропускаємо службові кнопки (гід розмірів, "переглянути розміри" тощо) —
        # це не реальні розміри.
        low_label = label.lower()
        if (
            not label
            or "переглянути" in low_label
            or "розмір" in low_label          # "ПЕРЕГЛЯНУТИ РОЗМІРИ", "таблиця розмірів"
            or "size guide" in low_label
            or "guide" in low_label
            or len(label) > 8                 # реальні розміри короткі (XS, M, 42, XXL)
        ):
            logger.debug("    · [%d] пропуск службової кнопки label=%r", idx, label)
            continue

        # Розмір НЕДОСТУПНИЙ, якщо є явні маркери disabled / sold-out.
        cl = classes.lower()
        unavailable = (
            disabled_attr is not None
            or aria_disabled == "true"
            or any(m in cl for m in ("is-disabled", "disabled", "out-of-stock",
                                     "sold-out", "unavailable", "is-out"))
            or any(m in aria_desc.lower() for m in ("розпродано", "немає", "sold out",
                                                    "out of stock", "недоступ"))
        )

        sizes.append({
            "size": label,
            "available": not unavailable,
        })

    logger.debug("← parse_sizes: готово, розмірів=%d", len(sizes))
    return sizes


def _dump_debug(driver, tag: str):
    """
    Зберігає скріншот + HTML сторінки в /tmp, щоб потім подивитись
    що саме бачив Selenium в момент збою (cookie-банер / капча /
    зовсім інша верстка тощо). Додатково логує driver.current_url,
    щоб зрозуміти, чи не було редиректу на капчу/головну.
    """
    ts = int(time.time())

    # ВАЖЛИВО: current_url логуємо ПЕРШИМ і окремо — навіть якщо скріншот/дамп
    # впадуть, ми маємо знати, куди реально завело браузер (редирект на капчу?).
    try:
        current_url = driver.current_url
        logger.warning("🌐 [%s] Current URL at failure: %s", tag, current_url)
        logger.warning("🏷 [%s] Title at failure: %r", tag, driver.title)
    except Exception as url_err:
        logger.warning("🌐 [%s] не вдалося прочитати current_url: %s", tag, url_err)

    # Детект блокування прямо в момент дампу — часто причина саме тут.
    try:
        blocked = detect_blocking(driver)
        if blocked:
            logger.error("🚫 [%s] У момент збою виявлено блокування: %s", tag, blocked)
    except Exception:
        pass

    try:
        dbg_dir = os.path.join(tempfile.gettempdir(), "bershka_debug")
        os.makedirs(dbg_dir, exist_ok=True)
        png_path = os.path.join(dbg_dir, f"bershka_debug_{tag}_{ts}.png")
        html_path = os.path.join(dbg_dir, f"bershka_debug_{tag}_{ts}.html")
        driver.save_screenshot(png_path)
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        logger.warning("🖼 [%s] DEBUG dump saved: %s | %s", tag, png_path, html_path)
    except Exception as dump_err:
        logger.warning("[%s] Не вдалося зберегти debug dump: %s", tag, dump_err)


def check_bershka_one(driver, url: str) -> str:
    """
    Перевіряє один товар Bershka через вже створений driver.
    Повертає ТЕКСТ, з якого потім моніторинг дістає розміри.
    """
    logger.info("======================================================")
    logger.info("▶ Checking BERSHKA URL: %s", url)

    # 1. Відкриваємо сторінку
    logger.debug("→ driver.get(%s)", url)
    try:
        driver.get(url)
        logger.debug("← driver.get завершено")
    except WebDriverException as e:
        # ВАЖЛИВО: якщо сесія ChromeDriver вже мертва (браузер впав),
        # тут прилетить "Connection refused" / "invalid session id".
        # Логуємо це явно, щоб відрізнити від "сайт не завантажився".
        logger.error(
            "❗ WebDriverException при driver.get(%s): %s (тип=%s)",
            url, e, type(e).__name__
        )
        return (
            f"🔗 <a href=\"{url}\">Посилання на товар</a>\n"
            f"⚠️ Помилка відкриття сторінки (driver/session error): {e}"
        )
    except Exception as e:
        # ловимо взагалі все, щоб один "мертвий" driver не зупинив весь чанк мовчки
        logger.exception("❗ НЕОЧІКУВАНА помилка при driver.get(%s): %s", url, e)
        return (
            f"🔗 <a href=\"{url}\">Посилання на товар</a>\n"
            f"⚠️ Непередбачена помилка: {e}"
        )

    # 2. Даємо сторінці прогрузитися (React рендерить асинхронно —
    #    2 сек часто замало, піднімаємо до 5, як у Zara)
    logger.debug("→ очікую рендер React (sleep 5s)")
    time.sleep(5)
    logger.debug("← sleep завершено")

    # 3. DEBUG: одразу логуємо реальний URL і довжину сторінки
    try:
        logger.info(
            "📄 Page loaded. current_url=%s title=%r html_len=%d",
            driver.current_url, driver.title, len(driver.page_source or "")
        )
    except Exception:
        pass

    # 4. ДЕТЕКТ БЛОКУВАННЯ — ПЕРЕД будь-яким парсингом.
    #    Якщо нас завернули на капчу/Cloudflare/Access Denied — немає сенсу
    #    чекати селектори, вони гарантовано впадуть у TimeoutException.
    blocked = detect_blocking(driver)
    if blocked:
        logger.error("🚫 Парсинг ПЕРЕРВАНО — сторінку заблоковано антибот-системою: %s", blocked)
        _dump_debug(driver, tag="antibot_block")
        return (
            f"🔗 <a href=\"{url}\">Посилання на товар</a>\n"
            f"🚫 Заблоковано антибот-системою ({blocked}). "
            f"Спробуйте пізніше / змініть проксі або user-agent."
        )

    # 5. Приймаємо cookies, як і на Zara
    accept_cookies_bershka(driver)

    # 6. Назва товару
    logger.debug("→ шукаю назву товару (h1 product-detail-name/title, timeout=10s)")
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
        logger.info("🏷 Назва товару: %r", product_name)
    except TimeoutException:
        logger.warning("⚠ BERSHKA назву товару не знайдено за 10с (TimeoutException)")
        _log_page_diagnostics(driver, stage="no_product_name")
    except Exception as e:
        logger.debug("BERSHKA product name not found: %s (%s)", e, type(e).__name__)

    # 7. Парсимо розміри
    logger.debug("→ виклик parse_sizes")
    sizes = parse_sizes(driver)

    # DEBUG: явно бачимо скільки розмірів реально знайшлось
    logger.info("🔎 BERSHKA parsed sizes count=%d raw=%s", len(sizes), sizes)

    # 8. Загальний статус
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
