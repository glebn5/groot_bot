from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from app.utils.keyboard import get_main_reply_keyboard

router = Router(name="common")


@router.message(CommandStart())
async def cmd_start(message: Message):
    welcome_text = (
        "👋 **Привет! Я Грут (Groot)** — твой личный мультимодальный ассистент.\n\n"
        "Я умею:\n"
        "• 📅 Показывать **планы на сегодня** (`/today`)\n"
        "• 🎯 Управлять **целями на месяц** (`/goals`)\n"
        "• 🔁 Вести **повторяющиеся задачи и привычки** (`/habits`)\n"
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
        "3️⃣ **Привычки:** Нажми «🔁 Привычки» или введи `/habits`.\n"
        "4️⃣ **Заметки:** Нажми «📝 Мои заметки» или введи `/notes`.\n"
        "5️⃣ **Настройки:** Нажми «⚙️ Настройки» или введи `/settings`.\n"
        "6️⃣ **Голосовые:** Наговори задачу или план, я расшифрую голос.\n"
        "7️⃣ **Фото/Документы:** Сфотографируй расписание, билет или чек — я извлеку даты."
    )
    await message.answer(help_text, reply_markup=get_main_reply_keyboard(), parse_mode="Markdown")


@router.message(Command("cancel"))
@router.message(F.text.in_({"cancel", "/cancel", "отмена", "Отмена", "❌ Отмена", "🔙 Отмена"}))
async def cmd_cancel_global(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state:
        await state.clear()
        await message.answer("❌ Действие отменено.", reply_markup=get_main_reply_keyboard(), parse_mode="Markdown")
    else:
        await message.answer("ℹ️ Нет активных действий для отмены.", reply_markup=get_main_reply_keyboard(), parse_mode="Markdown")
