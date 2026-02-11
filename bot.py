import asyncio
import logging
from html import escape

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

MONITOR_INTERVAL = 30  # сек
STATUS_PREVIEW_LINES = 25  # скільки рядків статусу показувати в дебазі, коли sizes не парсяться

DONE_SENTINEL = "__DONE__"


def build_notify_text(
    url: str,
    brand: str | None,
    status_text: str,
    available_sizes: set[str],
    wanted_sizes: set[str],
) -> str:
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

    return (
        f"🆕 <b>{title}</b>\n"
        f"Зміни в наявності!\n\n"
        f"🔗 <a href=\"{url}\">Посилання на товар</a>\n\n"
        f"📏 Доступні розміри зараз: <b>{sizes_list}</b>"
    )


async def monitor_loop(bot: Bot):
    logger = logging.getLogger("monitor")

    while True:
        try:
            rows = get_active_subscriptions()
            logger.info("ACTIVE SUBS: %s", len(rows))

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

            if not users_map:
                logger.info("No active subs. Sleep %ss", MONITOR_INTERVAL)
                await asyncio.sleep(MONITOR_INTERVAL)
                continue

            loop = asyncio.get_running_loop()

            for chat_id, items in users_map.items():
                urls = [u for (_, u, _, _) in items]
                if not urls:
                    continue

                logger.info("CHAT %s: urls=%s", chat_id, len(urls))

                url_to_item: dict[str, tuple[int, str | None, str | None]] = {
                    url: (sub_id, brand, sizes_raw)
                    for (sub_id, url, brand, sizes_raw) in items
                }

                queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()

                # семафор на відправку (щоб не блокувати і не ловити флуд)
                send_sem = asyncio.Semaphore(5)

                async def safe_send(text: str, url_for_log: str, sub_id_for_log: int):
                    async with send_sem:
                        try:
                            await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
                            logger.info("SEND OK chat=%s sub=%s url=%s", chat_id, sub_id_for_log, url_for_log)
                        except Exception as e:
                            logger.exception(
                                "SEND FAIL chat=%s sub=%s url=%s err=%s",
                                chat_id, sub_id_for_log, url_for_log, e
                            )

                async def sender_worker():
                    logger.info("sender_worker START chat=%s", chat_id)

                    while True:
                        url, new_status = await queue.get()

                        if url == DONE_SENTINEL:
                            logger.info("sender_worker DONE chat=%s", chat_id)
                            break

                        logger.info("WORKER GOT chat=%s url=%s status_len=%s", chat_id, url, len(new_status or ""))

                        meta = url_to_item.get(url)
                        if not meta:
                            logger.warning("WORKER: url not found in url_to_item: %s", url)
                            continue

                        sub_id, brand, sizes_raw = meta
                        old_status = last_status_map.get(sub_id, "")

                        if not new_status:
                            logger.warning("WORKER: empty status url=%s", url)
                            continue

                        new_available = extract_available_sizes(new_status)
                        old_available = extract_available_sizes(old_status)

                        if not new_available:
                            preview = "\n".join(new_status.splitlines()[:STATUS_PREVIEW_LINES])
                            logger.info(
                                "NO SIZES PARSED chat=%s sub=%s url=%s\nSTATUS PREVIEW:\n%s\n---END---",
                                chat_id, sub_id, url, preview
                            )

                        logger.info(
                            "COMPARE chat=%s sub=%s url=%s old=%s new=%s sizes_raw=%s",
                            chat_id, sub_id, url,
                            sorted(old_available), sorted(new_available),
                            sizes_raw
                        )

                        # оновлюємо last_status якщо змінився текст статусу
                        if new_status != old_status:
                            update_subscription_status(sub_id, new_status)
                            last_status_map[sub_id] = new_status
                            logger.info("DB UPDATED (status changed) chat=%s sub=%s url=%s", chat_id, sub_id, url)

                        # тригер повідомлення: тільки якщо змінилися розміри
                        if new_available == old_available:
                            logger.info("SKIP no size change chat=%s sub=%s url=%s", chat_id, sub_id, url)
                            continue

                        # якщо після зміни розмірів зараз пусто — не шлемо
                        if not new_available:
                            logger.info("SKIP sizes empty after change chat=%s sub=%s url=%s", chat_id, sub_id, url)
                            continue

                        # фільтр по sizes (якщо користувач вказав)
                        if sizes_raw:
                            wanted_sizes = {s.strip().upper() for s in sizes_raw.split(",") if s.strip()}
                        else:
                            wanted_sizes = set()  # означає "всі"

                        if wanted_sizes:
                            trigger = bool(new_available & wanted_sizes)
                        else:
                            trigger = True

                        logger.info(
                            "TRIGGER chat=%s sub=%s url=%s wanted=%s trigger=%s",
                            chat_id, sub_id, url, sorted(wanted_sizes), trigger
                        )

                        if not trigger:
                            continue

                        text = build_notify_text(
                            url=url,
                            brand=brand,
                            status_text=new_status,
                            available_sizes=new_available,
                            wanted_sizes=wanted_sizes,
                        )

                        logger.info("ABOUT TO SEND chat=%s sub=%s url=%s", chat_id, sub_id, url)
                        asyncio.create_task(safe_send(text, url, sub_id))

                sender_task = asyncio.create_task(sender_worker())

                def on_result(url: str, status: str):
                    logger.info("CB on_result CALLED chat=%s url=%s status_len=%s", chat_id, url, len(status or ""))
                    loop.call_soon_threadsafe(queue.put_nowait, (url, status))

                max_workers = 5
                logger.info("RUN selenium chat=%s urls=%s workers=%s", chat_id, len(urls), max_workers)

                # selenium працює в threadpool, on_result кидає результати в async-чергу
                await loop.run_in_executor(
                    None,
                    check_urls_for_user_parallel,
                    urls,
                    max_workers,
                    on_result
                )

                # ✅ головне: розбудити воркер і завершити його (інакше може зависнути на queue.get())
                loop.call_soon_threadsafe(queue.put_nowait, (DONE_SENTINEL, ""))

                await sender_task

        except Exception as e:
            logger.exception("Error in monitor_loop: %s", e)

        logger.info("SLEEP %ss", MONITOR_INTERVAL)
        await asyncio.sleep(MONITOR_INTERVAL)


async def main():
    setup_logging()
    init_db()

    bot = Bot(token=BOT_TOKEN)

    # швидка перевірка що бот живий
    me = await bot.get_me()
    print("BOT OK:", me.username, me.id)

    dp = Dispatcher()
    for r in all_routers:
        dp.include_router(r)

    asyncio.create_task(monitor_loop(bot))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
