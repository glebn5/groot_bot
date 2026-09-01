import logging
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from app.services.notes import notes_service

logger = logging.getLogger(__name__)
router = Router(name="notes")


async def render_notes_view(user_id: int):
    notes = await notes_service.get_notes(user_id)
    if not notes:
        return "🌴 **Твои сохранённые заметки:**\n\nУ тебя пока нет сохранённых заметок! Напиши мне «Запиши заметку ...» и я её сохраню ✨", None

    lines = ["🌴 **Твои сохранённые заметки:**\n"]
    buttons = []

    for idx, note in enumerate(notes, 1):
        content = note["content"]
        created_at = note["created_at"]
        note_id = note["id"]

        lines.append(f"{idx}️⃣ **{content}**")
        lines.append(f"   ⏱ _Создано: {created_at}_\n")

        buttons.append([
            InlineKeyboardButton(text=f"🗑 Удалить #{idx}", callback_data=f"del_note:{note_id}")
        ])

    buttons.append([
        InlineKeyboardButton(text="🧹 Очистить все заметки", callback_data="clear_all_notes")
    ])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    return "\n".join(lines), keyboard


@router.message(Command("notes"))
@router.message(F.text.in_({"📝 Мои заметки", "Мои заметки"}))
async def cmd_notes(message: Message):
    text, reply_markup = await render_notes_view(message.from_user.id)
    await message.answer(text, reply_markup=reply_markup, parse_mode="Markdown")


@router.callback_query(F.data.startswith("del_note:"))
async def process_delete_note(callback: CallbackQuery):
    note_id_str = callback.data.split(":", 1)[1]
    if note_id_str.isdigit():
        note_id = int(note_id_str)
        await notes_service.delete_note(note_id, callback.from_user.id)
        await callback.answer("Заметка удалена.")

    text, reply_markup = await render_notes_view(callback.from_user.id)
    await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode="Markdown")


@router.callback_query(F.data == "clear_all_notes")
async def process_clear_all_notes(callback: CallbackQuery):
    await notes_service.clear_notes(callback.from_user.id)
    await callback.answer("Все заметки очищены.")

    text, reply_markup = await render_notes_view(callback.from_user.id)
    await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode="Markdown")
