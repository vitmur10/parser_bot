import asyncio
import logging
from html import escape
from functools import partial

from aiogram import Bot, Dispatcher

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
MONITOR_INTERVAL = 30  # 1 хв (комент був не той)


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
    lines = status_text.splitlines()
    product_name = ""
    if len(lines) >= 2:
        product_name = lines[1].strip()

    brand_label = (brand or "").strip()
    if not brand_label:
        brand_label = detect_brand(url) or ""
    brand_label = brand_label.capitalize() if brand_label else "Товар"

    if product_name:
        title = f"{brand_label} — {escape(product_name)}"
    else:
        title = brand_label

    if wanted_sizes:
        show_sizes = sorted(available_sizes & wanted_sizes)
    else:
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
    - бере всі активні підписки
    - групує по chat_id
    - для кожного чату запускає Selenium перевірку
    - отримує результати по мірі готовності (через callback on_result)
    - одразу відправляє повідомлення з worker'а (не чекає завершення всіх url)
    """
    logger = logging.getLogger("monitor")
    send_sem = asyncio.Semaphore(5)  # 3-8 норм для одного чату

    async def safe_send(text: str):
        async with send_sem:
            try:
                await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
            except Exception as e:
                logger.warning("send failed chat=%s err=%s", chat_id, e)
    while True:
        try:
            rows = get_active_subscriptions()

            users_map: dict[int, list[tuple[int, str, str | None, str | None]]] = {}
            last_status_map: dict[int, str] = {}

            for r in rows:
                sub_id = r["id"]
                chat_id = r["chat_id"]
                url = r["url"]
                brand = r["brand"]
                last_status = r["last_status"] or ""
                sizes_raw = r.get("sizes") if isinstance(r, dict) else r["sizes"]

                last_status_map[sub_id] = last_status
                users_map.setdefault(chat_id, []).append((sub_id, url, brand, sizes_raw))

            loop = asyncio.get_running_loop()

            for chat_id, items in users_map.items():
                urls = [u for (_, u, _, _) in items]
                if not urls:
                    continue

                logger.info("Monitoring %s urls for chat %s", len(urls), chat_id)

                url_to_item: dict[str, tuple[int, str | None, str | None]] = {
                    url: (sub_id, brand, sizes_raw)
                    for (sub_id, url, brand, sizes_raw) in items
                }

                queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
                done = asyncio.Event()

                async def sender_worker():
                    logger.info("sender_worker START chat=%s", chat_id)

                    # kill-test: доводимо, що worker і queue живі
                    await queue.put(("__TEST__", "OK"))

                    while True:
                        if done.is_set() and queue.empty():
                            logger.info("sender_worker FINISH chat=%s", chat_id)
                            break

                        url, new_status = await queue.get()

                        if url == "__TEST__":
                            logger.info("sender_worker TEST OK (queue works) chat=%s", chat_id)
                            continue

                        logger.info(
                            "sender_worker GOT chat=%s url=%s status_len=%s",
                            chat_id, url, len(new_status or "")
                        )

                        meta = url_to_item.get(url)
                        if not meta:
                            logger.warning("sender_worker: url not found in url_to_item: %s", url)
                            continue

                        sub_id, brand, sizes_raw = meta
                        old_status = last_status_map.get(sub_id, "")

                        if not new_status:
                            continue

                        new_available = extract_available_sizes(new_status)
                        old_available = extract_available_sizes(old_status)

                        logger.info(
                            "DBG chat=%s sub=%s url=%s old=%s new=%s sizes_raw=%s",
                            chat_id, sub_id, url,
                            sorted(old_available), sorted(new_available),
                            sizes_raw
                        )

                        # Якщо набір доступних розмірів не змінився – нічого не шлемо
                        if new_available == old_available:
                            continue

                        # Оновлюємо статус в БД
                        update_subscription_status(sub_id, new_status)
                        last_status_map[sub_id] = new_status

                        if not new_available:
                            continue

                        if sizes_raw:
                            wanted_sizes = {
                                s.strip().upper()
                                for s in sizes_raw.split(",")
                                if s.strip()
                            }
                        else:
                            wanted_sizes: set[str] = set()

                        if not wanted_sizes:
                            trigger = True
                        else:
                            trigger = bool(new_available & wanted_sizes)

                        if not trigger:
                            continue

                        text = build_notify_text(
                            url=url,
                            brand=brand,
                            status_text=new_status,
                            available_sizes=new_available,
                            wanted_sizes=wanted_sizes,
                        )

                        try:
                            logger.info("SEND chat=%s sub=%s url=%s", chat_id, sub_id, url)
                            asyncio.create_task(safe_send(text))
                        except Exception as e:
                            logger.exception("Cannot send message chat=%s sub=%s url=%s err=%s", chat_id, sub_id, url, e)

                sender_task = asyncio.create_task(sender_worker())

                def on_result(url: str, status: str):
                    # callback викликається з потоку Selenium
                    logger.info(
                        "CB on_result CALLED chat=%s url=%s status_len=%s",
                        chat_id, url, len(status or "")
                    )
                    loop.call_soon_threadsafe(queue.put_nowait, (url, status))

                # ✅ ГОЛОВНЕ: запускаємо selenium і передаємо callback on_result
                await loop.run_in_executor(
                    None,
                    check_urls_for_user_parallel,
                    urls,
                    5,          # max_workers
                    on_result   # callback
                )

                done.set()
                await sender_task

        except Exception as e:
            logger.exception("Error in monitor_loop: %s", e)

        await asyncio.sleep(MONITOR_INTERVAL)


async def main():
    setup_logging()
    init_db()

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    for r in all_routers:
        dp.include_router(r)

    asyncio.create_task(monitor_loop(bot))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
