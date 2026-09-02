import logging
from typing import Tuple, Optional
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from app.services.notes import notes_service

logger = logging.getLogger(__name__)
router = Router(name="notes")

PAGE_SIZE = 5


class NoteAddForm(StatesGroup):
    waiting_for_content = State()


class NoteEditForm(StatesGroup):
    waiting_for_new_text = State()


async def safe_send_markdown(message: Message, text: str, reply_markup=None):
    try:
        await message.answer(text, reply_markup=reply_markup, parse_mode="Markdown")
    except Exception as e:
        logger.warning(f"Failed to send note with Markdown: {e}")
        await message.answer(text, reply_markup=reply_markup, parse_mode=None)


async def safe_edit_markdown(message: Message, text: str, reply_markup=None):
    try:
        await message.edit_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    except Exception as e:
        logger.warning(f"Failed to edit note with Markdown: {e}")
        await message.edit_text(text, reply_markup=reply_markup, parse_mode=None)


async def render_notes_view(user_id: int, page: int = 1) -> Tuple[str, InlineKeyboardMarkup]:
    notes = await notes_service.get_notes(user_id)
    if not notes:
        text = "🌴 **Твои сохранённые заметки:**\n\nУ тебя пока нет сохранённых заметок!\n\nНажми «➕ Добавить заметку» или просто напиши мне *«Запиши заметку ...»* ✨"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить заметку", callback_data="add_n_prompt:1")]
        ])
        return text, keyboard

    total_notes = len(notes)
    total_pages = max(1, (total_notes + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(1, min(page, total_pages))

    start_idx = (page - 1) * PAGE_SIZE
    end_idx = min(start_idx + PAGE_SIZE, total_notes)
    page_notes = notes[start_idx:end_idx]

    lines = ["🌴 **Твои сохранённые заметки:**\n"]
    for idx, note in enumerate(notes, 1):
        content = note["content"]
        created_at = note["created_at"]
        lines.append(f"{idx}️⃣ **{content}**")
        lines.append(f"   ⏱ _Создано: {created_at}_\n")

    lines.append("Нажмите на номер заметки для управления:")

    buttons = []
    # Row of note number buttons
    row = []
    for i, n in enumerate(page_notes, start=start_idx + 1):
        note_id = n["id"]
        row.append(InlineKeyboardButton(text=f" {i} ", callback_data=f"select_n:{note_id}:{page}:{i}"))
        if len(row) == 5:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    # Action buttons
    buttons.append([
        InlineKeyboardButton(text="➕ Добавить заметку", callback_data=f"add_n_prompt:{page}"),
        InlineKeyboardButton(text="🧹 Очистить все", callback_data="clear_notes_prompt")
    ])

    # Pagination buttons
    if total_pages > 1:
        pag_row = []
        if page > 1:
            pag_row.append(InlineKeyboardButton(text="⬅️ Пред.", callback_data=f"n_page:{page - 1}"))
        pag_row.append(InlineKeyboardButton(text=f"Стр. {page}/{total_pages}", callback_data="noop"))
        if page < total_pages:
            pag_row.append(InlineKeyboardButton(text="След. ➡️", callback_data=f"n_page:{page + 1}"))
        buttons.append(pag_row)

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    return "\n".join(lines), keyboard


async def render_note_detail_view(user_id: int, note_id: int, page: int = 1, idx: int = 1) -> Tuple[str, InlineKeyboardMarkup]:
    note = await notes_service.get_note_by_id(note_id, user_id)
    if not note:
        text = f"⚠️ Заметка #{idx} не найдена или была удалена."
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад к заметкам", callback_data=f"n_page:{page}")]
        ])
        return text, keyboard

    text = (
        f"📌 **Управление заметкой #{idx}:**\n\n"
        f"📝 **{note['content']}**\n\n"
        f"⏱ Создано: _{note['created_at']}_"
    )

    buttons = [
        [
            InlineKeyboardButton(text="✏️ Изменить", callback_data=f"edit_n_prompt:{note_id}:{page}:{idx}"),
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del_n:{note_id}:{page}")
        ],
        [
            InlineKeyboardButton(text="🔙 Назад к заметкам", callback_data=f"n_page:{page}")
        ]
    ]

    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


@router.message(Command("notes"))
@router.message(F.text.in_({"📝 Мои заметки", "Мои заметки"}))
async def cmd_notes(message: Message, state: FSMContext):
    await state.clear()
    text, reply_markup = await render_notes_view(message.from_user.id, page=1)
    await safe_send_markdown(message, text, reply_markup=reply_markup)


@router.callback_query(F.data.startswith("n_page:"))
async def process_n_page(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    page = int(callback.data.split(":")[1])
    text, reply_markup = await render_notes_view(callback.from_user.id, page=page)
    await safe_edit_markdown(callback.message, text, reply_markup=reply_markup)
    await callback.answer()


@router.callback_query(F.data.startswith("select_n:"))
async def process_select_note(callback: CallbackQuery):
    parts = callback.data.split(":")
    note_id = int(parts[1])
    page = int(parts[2]) if len(parts) > 2 else 1
    idx = int(parts[3]) if len(parts) > 3 else 1

    text, reply_markup = await render_note_detail_view(callback.from_user.id, note_id, page, idx)
    await safe_edit_markdown(callback.message, text, reply_markup=reply_markup)
    await callback.answer()


@router.callback_query(F.data.startswith("del_n:"))
@router.callback_query(F.data.startswith("del_note:"))
async def process_delete_note(callback: CallbackQuery):
    parts = callback.data.split(":")
    note_id = int(parts[1])
    page = int(parts[2]) if len(parts) > 2 else 1

    await notes_service.delete_note(note_id, callback.from_user.id)
    await callback.answer("Заметка удалена 🗑")

    text, reply_markup = await render_notes_view(callback.from_user.id, page=page)
    await safe_edit_markdown(callback.message, text, reply_markup=reply_markup)


@router.callback_query(F.data == "clear_notes_prompt")
async def process_clear_notes_prompt(callback: CallbackQuery):
    text = "⚠️ **Вы уверены, что хотите удалить ВСЕ сохранённые заметки?**\nЭто действие нельзя отменить!"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Да, очистить все", callback_data="confirm_clear_notes")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="n_page:1")]
    ])
    await safe_edit_markdown(callback.message, text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data == "confirm_clear_notes")
@router.callback_query(F.data == "clear_all_notes")
async def process_confirm_clear_notes(callback: CallbackQuery):
    await notes_service.clear_notes(callback.from_user.id)
    await callback.answer("Все заметки очищены 🧹")
    text, reply_markup = await render_notes_view(callback.from_user.id, page=1)
    await safe_edit_markdown(callback.message, text, reply_markup=reply_markup)


@router.callback_query(F.data.startswith("add_n_prompt:"))
async def process_add_note_prompt(callback: CallbackQuery, state: FSMContext):
    page = int(callback.data.split(":")[1])
    await state.set_state(NoteAddForm.waiting_for_content)
    await state.update_data(page=page)

    text = (
        "📝 **Новая заметка:**\n\n"
        "Отправьте текст заметки одним сообщением.\n\n"
        "_(нажмите кнопку ниже или отправьте /cancel для отмены)_"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"n_page:{page}")]
    ])
    await safe_edit_markdown(callback.message, text, reply_markup=keyboard)
    await callback.answer()


@router.message(Command("cancel"), NoteAddForm.waiting_for_content)
@router.message(F.text.in_({"cancel", "/cancel", "отмена", "Отмена", "❌ Отмена", "🔙 Отмена"}), NoteAddForm.waiting_for_content)
async def process_cancel_add_note(message: Message, state: FSMContext):
    data = await state.get_data()
    page = data.get("page", 1)
    await state.clear()
    text, reply_markup = await render_notes_view(message.from_user.id, page=page)
    await safe_send_markdown(message, "❌ Добавление заметки отменено.", reply_markup=reply_markup)


@router.message(NoteAddForm.waiting_for_content)
async def process_save_note_content(message: Message, state: FSMContext):
    data = await state.get_data()
    page = data.get("page", 1)
    content = message.text.strip() if message.text else ""

    if content.lower() in ["/cancel", "cancel", "отмена", "❌ отмена", "🔙 отмена"]:
        await state.clear()
        text, reply_markup = await render_notes_view(message.from_user.id, page=page)
        await safe_send_markdown(message, "❌ Добавление заметки отменено.", reply_markup=reply_markup)
        return

    await state.clear()
    if content:
        await notes_service.add_note(message.from_user.id, content)
        await message.answer(f"📌 **Заметка сохранена!**\n_«{content}»_")

    text, reply_markup = await render_notes_view(message.from_user.id, page=page)
    await safe_send_markdown(message, text, reply_markup=reply_markup)


@router.callback_query(F.data.startswith("edit_n_prompt:"))
async def process_edit_note_prompt(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split(":")
    note_id = int(parts[1])
    page = int(parts[2]) if len(parts) > 2 else 1
    idx = int(parts[3]) if len(parts) > 3 else 1

    note = await notes_service.get_note_by_id(note_id, callback.from_user.id)
    if not note:
        await callback.answer("⚠️ Заметка не найдена.", show_alert=True)
        return

    await state.set_state(NoteEditForm.waiting_for_new_text)
    await state.update_data(note_id=note_id, page=page, idx=idx)

    text = (
        f"✏️ **Редактирование заметки #{idx}:**\n\n"
        f"Текущий текст:\n_«{note['content']}»_\n\n"
        f"Отправьте новый текст заметки одним сообщением.\n\n"
        f"_(нажмите кнопку ниже или отправьте /cancel для отмены)_"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"select_n:{note_id}:{page}:{idx}")]
    ])
    await safe_edit_markdown(callback.message, text, reply_markup=keyboard)
    await callback.answer()


@router.message(Command("cancel"), NoteEditForm.waiting_for_new_text)
@router.message(F.text.in_({"cancel", "/cancel", "отмена", "Отмена", "❌ Отмена", "🔙 Отмена"}), NoteEditForm.waiting_for_new_text)
async def process_cancel_edit_note(message: Message, state: FSMContext):
    data = await state.get_data()
    note_id = data.get("note_id")
    page = data.get("page", 1)
    idx = data.get("idx", 1)
    await state.clear()

    if note_id:
        text, reply_markup = await render_note_detail_view(message.from_user.id, note_id, page, idx)
        await safe_send_markdown(message, "❌ Редактирование заметки отменено.", reply_markup=reply_markup)
    else:
        text, reply_markup = await render_notes_view(message.from_user.id, page=page)
        await safe_send_markdown(message, text, reply_markup=reply_markup)


@router.message(NoteEditForm.waiting_for_new_text)
async def process_save_edited_note(message: Message, state: FSMContext):
    data = await state.get_data()
    note_id = data.get("note_id")
    page = data.get("page", 1)
    idx = data.get("idx", 1)
    new_text = message.text.strip() if message.text else ""

    if new_text.lower() in ["/cancel", "cancel", "отмена", "❌ отмена", "🔙 отмена"]:
        await state.clear()
        text, reply_markup = await render_note_detail_view(message.from_user.id, note_id, page, idx)
        await safe_send_markdown(message, "❌ Редактирование отменено.", reply_markup=reply_markup)
        return

    await state.clear()
    if note_id and new_text:
        await notes_service.update_note(note_id, message.from_user.id, new_text)
        await message.answer(f"✨ **Заметка #{idx} обновлена!**")

    text, reply_markup = await render_note_detail_view(message.from_user.id, note_id, page, idx)
    await safe_send_markdown(message, text, reply_markup=reply_markup)
