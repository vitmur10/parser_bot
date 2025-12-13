import asyncio
import logging
from aiogram import Bot, Dispatcher
from html import escape
from config import BOT_TOKEN, setup_logging
from handlers import all_routers
from db import (
    init_db,
    get_active_subscriptions,
    update_subscription_status,
)
from services.selenium_parser import check_urls_for_user_parallel
from utils.urls import extract_available_sizes, detect_brand

# інтервал між циклами моніторингу, сек
# можеш сміливо поставити 300 (5 хв) або 60 (1 хв), але стеж за навантаженням
MONITOR_INTERVAL = 60  # 10 хвилин


def build_notify_text(
        url: str,
        brand: str | None,
        status_text: str,
        available_sizes: set[str],
        wanted_sizes: set[str],
) -> str:
    """
    Формує фінальне повідомлення:
    - в заголовку: Магазин + назва товару
    - у списку розмірів: або тільки потрібні, або всі (якщо користувач не вказав).
    """
    # --- дістаємо назву товару з status_text ---
    # у наших парсерів:
    #   1-й рядок: <b>🧵 Zara</b> / <b>🧥 Bershka</b>
    #   2-й рядок: назва товару
    lines = status_text.splitlines()
    product_name = ""
    if len(lines) >= 2:
        product_name = lines[1].strip()

    # --- бренд ---
    brand_label = (brand or "").strip()
    if not brand_label:
        brand_label = detect_brand(url) or ""
    brand_label = brand_label.capitalize() if brand_label else "Товар"

    # заголовок: "Bershka — Джинси-суперскіни..."
    if product_name:
        title = f"{brand_label} — {escape(product_name)}"
    else:
        title = brand_label

    # --- які розміри показувати ---
    if wanted_sizes:
        # показуємо тільки ті, що йому цікаві
        show_sizes = sorted(available_sizes & wanted_sizes)
    else:
        # якщо не вказував розміри — показуємо всі доступні
        show_sizes = sorted(available_sizes)

    sizes_list = ", ".join(show_sizes) if show_sizes else "—"

    text = (
        f"🆕 <b>{title}</b>\n"
        f"Зміни в наявності!\n\n"
        f"🔗 <a href=\"{url}\">Посилання на товар</a>\n\n"
        f"📏 Доступні розміри зараз: <b>{sizes_list}</b>"
    )
    return text


async def monitor_loop(bot: Bot):
    """
    Фоновий моніторинг:
    - раз у MONITOR_INTERVAL секунд дістає всі активні підписки
    - групує їх по chat_id
    - для кожного чату паралельно перевіряє всі URL через Selenium
    - надсилає ОКРЕМЕ повідомлення по кожному товару,
      тільки якщо змінився набір доступних розмірів
      і є перетин з відстежуваними розмірами.
    """
    logger = logging.getLogger("monitor")

    while True:
        try:
            rows = get_active_subscriptions()

            # chat_id -> список (sub_id, url, brand, sizes_raw)
            users_map: dict[int, list[tuple[int, str, str | None, str | None]]] = {}
            # sub_id -> last_status (текст)
            last_status_map: dict[int, str] = {}

            for r in rows:
                sub_id = r["id"]
                chat_id = r["chat_id"]
                url = r["url"]
                brand = r["brand"]
                last_status = r["last_status"] or ""
                # може бути sqlite.Row або dict
                sizes_raw = r.get("sizes") if isinstance(r, dict) else r["sizes"]

                last_status_map[sub_id] = last_status
                users_map.setdefault(chat_id, []).append(
                    (sub_id, url, brand, sizes_raw)
                )

            loop = asyncio.get_running_loop()

            for chat_id, items in users_map.items():
                urls = [u for (_, u, _, _) in items]
                if not urls:
                    continue

                logger.info("Monitoring %s urls for chat %s", len(urls), chat_id)

                # всередині check_urls_for_user_parallel вже паралельні драйвери
                status_map = await loop.run_in_executor(
                    None,
                    check_urls_for_user_parallel,
                    urls,
                )

                for sub_id, url, brand, sizes_raw in items:
                    new_status = status_map.get(url)
                    old_status = last_status_map.get(sub_id, "")

                    if not new_status:
                        continue

                    # --- Порівнюємо не текст, а множини доступних розмірів ---
                    new_available = extract_available_sizes(new_status)
                    old_available = extract_available_sizes(old_status)

                    # Якщо набір доступних розмірів не змінився – нічого не шлемо
                    if new_available == old_available:
                        continue

                    # Оновлюємо статус в БД для історії
                    update_subscription_status(sub_id, new_status)

                    # Якщо тепер немає жодного доступного розміру – теж мовчимо
                    if not new_available:
                        continue

                    # --- Розміри, за якими юзер хоче слідкувати ---
                    if sizes_raw:
                        wanted_sizes = {
                            s.strip().upper()
                            for s in sizes_raw.split(",")
                            if s.strip()
                        }
                    else:
                        wanted_sizes: set[str] = set()  # означає "всі розміри"

                    # --- Чи треба надсилати повідомлення? ---
                    if not wanted_sizes:
                        # користувач не задав конкретні розміри:
                        # якщо є хоч один доступний – шлемо
                        trigger = True
                    else:
                        # є перетин потрібних з доступними?
                        trigger = bool(new_available & wanted_sizes)

                    if not trigger:
                        continue

                    # --- Будуємо фінальний текст для користувача ---
                    text = build_notify_text(
                        url=url,
                        brand=brand,
                        status_text=new_status,
                        available_sizes=new_available,
                        wanted_sizes=wanted_sizes,
                    )

                    try:
                        await bot.send_message(
                            chat_id=chat_id,
                            text=text,
                            parse_mode="HTML",
                            # превʼю не вимикаємо – картку додає Telegram
                        )
                    except Exception as e:
                        logger.warning(
                            "Cannot send message to chat %s: %s", chat_id, e
                        )

        except Exception as e:
            logger.exception("Error in monitor_loop: %s", e)

        await asyncio.sleep(MONITOR_INTERVAL)


async def main():
    setup_logging()
    init_db()

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    # підключаємо всі роутери (start/help, links, subscriptions, chatid)
    for r in all_routers:
        dp.include_router(r)

    # запускаємо фоновий монітор
    asyncio.create_task(monitor_loop(bot))

    # запускаємо самого бота
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
