from aiogram import Router
from aiogram.filters import CommandStart, Command
from aiogram.types import Message

router = Router(name="common")


@router.message(CommandStart())
async def cmd_start(message: Message):
    welcome_text = (
        "👋 **Привет! Я Пятница (Friday)** — твой личный мультимодальный ассистент.\n\n"
        "Я умею:\n"
        "• 📝 Добавлять задачи в ежедневные заметки Obsidian (Mail.ru WebDAV)\n"
        "• 📅 Создавать события в Google Календаре\n"
        "• ⏰ Ставить напоминания в Telegram на нужное время\n"
        "• 🎙 Принимать голосовые сообщения и расшифровывать их через Groq Whisper\n"
        "• 📸 Анализировать фото, расписания, чеки и справки через Vision API\n\n"
        "Просто напиши мне текстом, отправь голосовое или пришли картинку!"
    )
    await message.answer(welcome_text, parse_mode="Markdown")


@router.message(Command("help"))
async def cmd_help(message: Message):
    help_text = (
        "💡 **Инструкция по использованию:**\n\n"
        "1️⃣ **Текст:** 'Добавь в Obsidian задачу купить продукты завтра в 15:00'\n"
        "2️⃣ **Голосовые:** Наговори задачу или план, я расшифрую голос и извлеку действия.\n"
        "3️⃣ **Фото/Документы:** Сфотографируй расписание, билет или чек — я извлеку даты и поставлю напоминания."
    )
    await message.answer(help_text, parse_mode="Markdown")
