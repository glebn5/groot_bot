from aiogram import Router
from aiogram.filters import CommandStart, Command
from aiogram.types import Message
from app.utils.keyboard import get_main_reply_keyboard

router = Router(name="common")


@router.message(CommandStart())
async def cmd_start(message: Message):
    welcome_text = (
        "👋 **Привет! Я Грут (Groot)** — твой личный мультимодальный ассистент.\n\n"
        "Я умею:\n"
        "• 📅 Позывать **планы на сегодня** (`/today`)\n"
        "• 🎯 Управлять **целями на месяц** (`/goals`)\n"
        "• 📝 Добавлять задачи в ежедневные заметки Obsidian (Mail.ru WebDAV)\n"
        "• 📅 Создавать события в Google Календаре\n"
        "• ⏰ Ставить напоминания в Telegram на нужное время\n"
        "• 🎙 Принимать голосовые сообщения и расшифровывать их через Groq Whisper\n"
        "• 📸 Анализировать фото, расписания, чеки и справки через Vision API\n\n"
        "Используй кнопки меню ниже или просто напиши мне текстом!"
    )
    await message.answer(welcome_text, reply_markup=get_main_reply_keyboard(), parse_mode="Markdown")


@router.message(Command("help"))
async def cmd_help(message: Message):
    help_text = (
        "💡 **Инструкция по использованию:**\n\n"
        "1️⃣ **Планы на сегодня:** Нажми «📅 Планы на сегодня» или введи `/today`.\n"
        "2️⃣ **Цели на месяц:** Нажми «🎯 Цели на месяц» или введи `/goals`.\n"
        "3️⃣ **Заметки:** Нажми «📝 Мои заметки» или введи `/notes`.\n"
        "4️⃣ **Настройки:** Нажми «⚙️ Настройки» или введи `/settings`.\n"
        "5️⃣ **Голосовые:** Наговори задачу или план, я расшифрую голос.\n"
        "6️⃣ **Фото/Документы:** Сфотографируй расписание, билет или чек — я извлеку даты."
    )
    await message.answer(help_text, reply_markup=get_main_reply_keyboard(), parse_mode="Markdown")
