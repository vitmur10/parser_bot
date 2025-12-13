from aiogram import Router
from aiogram.filters import CommandStart, Command
from aiogram.types import Message

from config import MAX_PER_BRAND

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "Привіт! 👋\n\n"
        "Надішли мені посилання на товари Zara / Bershka (до 50 на бренд) — "
        "я перевірю наявність і поверну результат.\n\n"
        "Можеш просто вставити список посилань одним повідомленням."
    )


@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "Я бот-парсер наявності товарів Zara / Bershka.\n\n"
        "1️⃣ Скопіюй посилання на товари з сайту.\n"
        "2️⃣ Надішли їх сюди (можна багато одразу).\n"
        "3️⃣ Я поверну статус: в наявності / немає / помилка.\n\n"
        f"Ліміт: до {MAX_PER_BRAND} посилань на кожен бренд за один запит."
    )


@router.message(Command("chatid"))
async def cmd_chatid(message: Message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    chat_type = message.chat.type

    await message.answer(
        f"<b>Chat ID:</b> <code>{chat_id}</code>\n"
        f"<b>User ID:</b> <code>{user_id}</code>\n"
        f"<b>Тип чату:</b> {chat_type}"
    )