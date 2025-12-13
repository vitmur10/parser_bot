from aiogram import Router, F
from aiogram.types import Message
from services.selenium_parser import check_many_products_selenium_parallel, format_results
from utils.urls import extract_urls, detect_brand
from db import add_subscription
import asyncio

router = Router()


@router.message(
    F.text
    & ~F.via_bot
    & ~F.text.startswith("/")   # ⬅️ ВАЖЛИВО: не ловимо команди!
)
async def handle_links(message: Message):
    urls = extract_urls(message.text)
    if not urls:
        await message.answer(
            "Я не знайшов жодного посилання в повідомленні 😔\n"
            "Надішли, будь ласка, URL-адреси товарів Zara або Bershka."
        )
        return

    await message.answer(
        f"Знайшов <b>{len(urls)}</b> посилань.", parse_mode="HTML"
    )

    loop = asyncio.get_running_loop()
    results = await loop.run_in_executor(
        None,
        check_many_products_selenium_parallel,
        urls,
    )

    blocks = format_results(results)
    for block in blocks:
        await message.answer(block, parse_mode="HTML")

    # Зберігання в БД
    user_id = message.from_user.id
    chat_id = message.chat.id

    for brand_key in ["zara", "bershka", "other"]:
        for url, status in results.get(brand_key, []):
            brand = detect_brand(url)
            add_subscription(
                user_id=user_id,
                chat_id=chat_id,
                url=url,
                brand=brand,
                last_status=status,
            )

    await message.answer(
        "🔔 Я додав ці посилання в моніторинг.\n"
        "Подивитися список: /my_links\n"
        "Видалити одне: /del <id>\n"
        "Видалити всі: /del_all"
    )
