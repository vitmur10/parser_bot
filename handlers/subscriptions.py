from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from db import get_user_subscriptions, add_subscription
from handlers.subscriptions_repo import delete_subscription, delete_all_for_user
from utils.urls import detect_brand
router = Router()


@router.message(Command("my_links"))
async def cmd_my_links(message: Message):
    user_id = message.from_user.id
    subs = get_user_subscriptions(user_id)

    if not subs:
        await message.answer("У тебе поки немає збережених посилань для моніторингу.")
        return

    # Формуємо текст і ріжемо на шматки, щоб не перевищити ліміт Telegram (~4096)
    chunks: list[str] = []
    current = "Твої посилання під моніторингом:\n\n"

    def add_line(line: str):
        nonlocal current, chunks
        # 3500 беремо з запасом, щоб не впертись у 4096
        if len(current) + len(line) + 1 > 3500:
            chunks.append(current)
            current = ""
        current += line + "\n"

    for row in subs:
        status = row["last_status"] or "—"
        active = "✅ активне" if row["is_active"] else "⏹ вимкнене"

        block = (
            f"<b>ID:</b> <code>{row['id']}</code>\n"
            f"<b>URL:</b> {row['url']}\n"
            f"<b>Бренд:</b> {row['brand'] or '—'}\n"
            f"<b>Статус:</b> {status}\n"
            f"<b>Стан:</b> {active}\n"
            f"<b>Створено:</b> {row['created_at']}\n"
            "----------------------------\n"
        )
        add_line(block)

    if current.strip():
        chunks.append(current)

    # Відправляємо по шматках
    for chunk in chunks:
        await message.answer(chunk, parse_mode="HTML")


@router.message(Command("del"))
async def cmd_del(message: Message):
    """
    /del 12
    """
    user_id = message.from_user.id
    parts = message.text.strip().split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(
            "Використання: <code>/del ID</code>\nНапр.: <code>/del 12</code>",
            parse_mode="HTML",
        )
        return

    try:
        sub_id = int(parts[1])
    except ValueError:
        await message.answer("ID має бути числом. Приклад: <code>/del 12</code>")
        return

    ok = delete_subscription(sub_id=sub_id, user_id=user_id)
    if ok:
        await message.answer(f"✅ Посилання з ID {sub_id} повністю видалено з моніторингу.")
    else:
        await message.answer("Я не знайшов активного посилання з таким ID, яке належить тобі.")


@router.message(Command("del_all"))
async def cmd_del_all(message: Message):
    user_id = message.from_user.id
    deleted_count = delete_all_for_user(user_id)
    if deleted_count:
        await message.answer(f"⏹ Я повністю видалив {deleted_count} посилань з моніторингу.")
    else:
        await message.answer("У тебе немає збережених посилань для видалення.")


@router.message(Command("add"))
async def cmd_add(message: Message):
    """
    /add <url> [size1 size2 size3]

    Приклади:
    /add https://www.zara.com/...
    /add https://www.bershka.com/... M L XL
    """
    parts = message.text.strip().split()

    if len(parts) < 2:
        await message.answer(
            "❗ Формат:\n"
            "<code>/add URL</code>\n"
            "<code>/add URL M L XL</code> — слідкувати тільки за розмірами",
            parse_mode="HTML",
        )
        return

    url = parts[1].strip()
    brand = detect_brand(url)

    if brand not in {"zara", "bershka"}:
        await message.answer("❗ Підтримуються лише Zara та Bershka")
        return

    # Розміри, якщо передані
    sizes = ""
    if len(parts) > 2:
        sizes_list = [s.upper() for s in parts[2:]]
        sizes = ",".join(sizes_list)

    # 👇 ГОЛОВНЕ: додаємо параметр last_status=None
    add_subscription(
        user_id=message.from_user.id,
        chat_id=message.chat.id,
        url=url,
        brand=brand,
        last_status=None,   # перший запуск → нічого ще не знаємо
        sizes=sizes or None,
    )

    if sizes:
        await message.answer(
            f"🔔 Підписка додана!\n"
            f"📦 Магазин: <b>{brand.title()}</b>\n"
            f"🔗 <a href=\"{url}\">Перейти до товару</a>\n"
            f"📏 Моніторю розміри: <b>{sizes.replace(',', ', ')}</b>",
            parse_mode="HTML",
            disable_web_page_preview=False  # щоб було превʼю товару
        )
    else:
        await message.answer(
            f"🔔 Підписка додана!\n"
            f"📦 Магазин: <b>{brand.title()}</b>\n"
            f"🔗 <a href=\"{url}\">Перейти до товару</a>\n"
            f"📏 Моніторю всі розміри",
            parse_mode="HTML",
            disable_web_page_preview=False  # також залишаємо превʼю
        )