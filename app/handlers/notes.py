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


class FolderAddForm(StatesGroup):
    waiting_for_name = State()


class NoteAddForm(StatesGroup):
    waiting_for_content = State()


class NoteEditForm(StatesGroup):
    waiting_for_new_text = State()


def format_copyable_text(text: str) -> str:
    clean = str(text or "").strip()
    if "\n" in clean or "`" in clean:
        clean_escaped = clean.replace("```", "` ` `")
        return f"```\n{clean_escaped}\n```"
    return f"`{clean}`"


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


# --- 1. SECTIONS / FOLDERS VIEW (MAIN ENTRY POINT) ---

async def render_folders_view(user_id: int) -> Tuple[str, InlineKeyboardMarkup]:
    folders = await notes_service.get_folders(user_id)
    unsorted_count = await notes_service.get_unsorted_notes_count(user_id)

    lines = ["🌴 **Твои разделы заметок:**\n"]
    buttons = []

    if not folders and unsorted_count == 0:
        lines.append("У тебя пока нет созданных разделов и заметок!\nНажми «➕ Создать раздел» или «➕ Добавить заметку» ниже ✨\n")
    else:
        folder_num = 1
        row = []
        for f in folders:
            f_id = f["id"]
            name = f["name"]
            count = f["note_count"]
            lines.append(f"📁 {folder_num}. **{name}** ({count} зам.)")
            row.append(InlineKeyboardButton(text=f"📁 {folder_num}", callback_data=f"open_f:{f_id}"))
            if len(row) == 4:
                buttons.append(row)
                row = []
            folder_num += 1

        if unsorted_count > 0 or not folders:
            lines.append(f"📥 **Без раздела / Неотсортированное** ({unsorted_count} зам.)")
            row.append(InlineKeyboardButton(text="📥 Без раздела", callback_data="open_f:0"))

        if row:
            buttons.append(row)

    lines.append("\nВыберите раздел выбрав кнопку выше, или воспользуйтесь управлением:")

    # Vertical action buttons per design requirement
    buttons.append([InlineKeyboardButton(text="➕ Создать раздел", callback_data="add_f_prompt")])
    buttons.append([InlineKeyboardButton(text="➕ Добавить заметку", callback_data="add_n_prompt:0")])
    buttons.append([InlineKeyboardButton(text="📝 Все заметки (списком)", callback_data="open_f:-1")])
    if folders:
        buttons.append([InlineKeyboardButton(text="⚙️ Настройки разделов", callback_data="f_manage")])
    buttons.append([InlineKeyboardButton(text="🧹 Очистить все заметки", callback_data="clear_notes_prompt")])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    return "\n".join(lines), keyboard


# --- 2. FOLDER NOTES LIST VIEW ---

async def render_notes_view(user_id: int, folder_id: int = 0, page: int = 1) -> Tuple[str, InlineKeyboardMarkup]:
    folder_name = "Без раздела"
    if folder_id > 0:
        folder = await notes_service.get_folder_by_id(folder_id, user_id)
        folder_name = folder["name"] if folder else "Раздел"
        notes = await notes_service.get_notes(user_id, folder_id=folder_id)
    elif folder_id == -1:
        folder_name = "Все заметки"
        notes = await notes_service.get_notes(user_id)
    else:
        # folder_id == 0 -> unsorted
        folder_name = "Без раздела"
        notes = await notes_service.get_notes(user_id, unsorted_only=True)

    if not notes:
        text = f"🌴 **Заметки раздела «{folder_name}»:**\n\nВ этом разделе пока нет заметок!"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить заметку", callback_data=f"add_n_prompt:{folder_id}")],
            [InlineKeyboardButton(text="📁 Назад к разделам", callback_data="show_folders")]
        ])
        return text, keyboard

    total_notes = len(notes)
    total_pages = max(1, (total_notes + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(1, min(page, total_pages))

    start_idx = (page - 1) * PAGE_SIZE
    end_idx = min(start_idx + PAGE_SIZE, total_notes)
    page_notes = notes[start_idx:end_idx]

    lines = [f"🌴 **Заметки раздела «{folder_name}»:**\n"]
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
        row.append(InlineKeyboardButton(text=f" {i} ", callback_data=f"select_n:{note_id}:{folder_id}:{page}:{i}"))
        if len(row) == 5:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    # Vertical action buttons per design requirement
    buttons.append([InlineKeyboardButton(text="➕ Добавить заметку", callback_data=f"add_n_prompt:{folder_id}")])
    buttons.append([InlineKeyboardButton(text="🧹 Очистить этот раздел", callback_data=f"clear_folder_prompt:{folder_id}")])
    buttons.append([InlineKeyboardButton(text="📁 Назад к разделам", callback_data="show_folders")])

    # Pagination buttons
    if total_pages > 1:
        pag_row = []
        if page > 1:
            pag_row.append(InlineKeyboardButton(text="⬅️ Пред.", callback_data=f"n_page:{folder_id}:{page - 1}"))
        pag_row.append(InlineKeyboardButton(text=f"Стр. {page}/{total_pages}", callback_data="noop"))
        if page < total_pages:
            pag_row.append(InlineKeyboardButton(text="След. ➡️", callback_data=f"n_page:{folder_id}:{page + 1}"))
        buttons.append(pag_row)

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    return "\n".join(lines), keyboard


# --- 3. NOTE DETAIL VIEW ---

async def render_note_detail_view(user_id: int, note_id: int, folder_id: int = 0, page: int = 1, idx: int = 1) -> Tuple[str, InlineKeyboardMarkup]:
    note = await notes_service.get_note_by_id(note_id, user_id)
    if not note:
        text = f"⚠️ Заметка #{idx} не найдена или была удалена."
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад к заметкам", callback_data=f"n_page:{folder_id}:{page}")]
        ])
        return text, keyboard

    folder_name = "Без раздела"
    if note.get("folder_id"):
        f_info = await notes_service.get_folder_by_id(note["folder_id"], user_id)
        if f_info:
            folder_name = f_info["name"]

    text = (
        f"📌 **Управление заметкой #{idx}:**\n\n"
        f"📝 **{note['content']}**\n\n"
        f"📁 Раздел: **{folder_name}**\n"
        f"⏱ Создано: _{note['created_at']}_"
    )

    buttons = [
        [
            InlineKeyboardButton(text="✏️ Изменить", callback_data=f"edit_n_prompt:{note_id}:{folder_id}:{page}:{idx}"),
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del_n:{note_id}:{folder_id}:{page}")
        ],
        [
            InlineKeyboardButton(text="📁 Переместить в раздел", callback_data=f"move_n_prompt:{note_id}:{folder_id}:{page}:{idx}")
        ],
        [
            InlineKeyboardButton(text="🔙 Назад к заметкам", callback_data=f"n_page:{folder_id}:{page}")
        ]
    ]

    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


# --- 4. FOLDER MANAGEMENT SCREEN ---

async def render_folder_management_view(user_id: int) -> Tuple[str, InlineKeyboardMarkup]:
    folders = await notes_service.get_folders(user_id)
    if not folders:
        text = "⚙️ **Управление разделами:**\n\nУ вас пока нет созданных разделов."
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Создать раздел", callback_data="add_f_prompt")],
            [InlineKeyboardButton(text="📁 Назад к разделам", callback_data="show_folders")]
        ])
        return text, keyboard

    lines = ["⚙️ **Управление разделами заметок:**\n"]
    buttons = []

    for idx, f in enumerate(folders, 1):
        f_id = f["id"]
        name = f["name"]
        count = f["note_count"]
        lines.append(f"{idx}. 📁 **{name}** — _{count} заметок_")
        buttons.append([
            InlineKeyboardButton(text=f"🗑 Удалить раздел «{name}»", callback_data=f"del_f_prompt:{f_id}")
        ])

    buttons.append([InlineKeyboardButton(text="➕ Создать раздел", callback_data="add_f_prompt")])
    buttons.append([InlineKeyboardButton(text="📁 Назад к разделам", callback_data="show_folders")])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    return "\n".join(lines), keyboard


# --- HANDLERS & ROUTING ---

@router.message(Command("notes"))
@router.message(F.text.in_({"📝 Мои заметки", "Мои заметки"}))
async def cmd_notes(message: Message, state: FSMContext):
    await state.clear()
    text, reply_markup = await render_folders_view(message.from_user.id)
    await safe_send_markdown(message, text, reply_markup=reply_markup)


@router.callback_query(F.data == "show_folders")
async def process_show_folders(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    text, reply_markup = await render_folders_view(callback.from_user.id)
    await safe_edit_markdown(callback.message, text, reply_markup=reply_markup)
    await callback.answer()


@router.callback_query(F.data.startswith("open_f:"))
async def process_open_folder(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    folder_id = int(callback.data.split(":")[1])
    text, reply_markup = await render_notes_view(callback.from_user.id, folder_id=folder_id, page=1)
    await safe_edit_markdown(callback.message, text, reply_markup=reply_markup)
    await callback.answer()


@router.callback_query(F.data.startswith("n_page:"))
async def process_n_page(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    parts = callback.data.split(":")
    folder_id = int(parts[1])
    page = int(parts[2]) if len(parts) > 2 else 1
    text, reply_markup = await render_notes_view(callback.from_user.id, folder_id=folder_id, page=page)
    await safe_edit_markdown(callback.message, text, reply_markup=reply_markup)
    await callback.answer()


@router.callback_query(F.data.startswith("select_n:"))
async def process_select_note(callback: CallbackQuery):
    parts = callback.data.split(":")
    note_id = int(parts[1])
    folder_id = int(parts[2]) if len(parts) > 2 else 0
    page = int(parts[3]) if len(parts) > 3 else 1
    idx = int(parts[4]) if len(parts) > 4 else 1

    text, reply_markup = await render_note_detail_view(callback.from_user.id, note_id, folder_id, page, idx)
    await safe_edit_markdown(callback.message, text, reply_markup=reply_markup)
    await callback.answer()


@router.callback_query(F.data.startswith("del_n:"))
@router.callback_query(F.data.startswith("del_note:"))
async def process_delete_note(callback: CallbackQuery):
    parts = callback.data.split(":")
    note_id = int(parts[1])
    folder_id = int(parts[2]) if len(parts) > 2 else 0
    page = int(parts[3]) if len(parts) > 3 else 1

    await notes_service.delete_note(note_id, callback.from_user.id)
    await callback.answer("Заметка удалена 🗑")

    text, reply_markup = await render_notes_view(callback.from_user.id, folder_id=folder_id, page=page)
    await safe_edit_markdown(callback.message, text, reply_markup=reply_markup)


# --- FOLDER CREATION & MANAGEMENT HANDLERS ---

@router.callback_query(F.data == "f_manage")
async def process_folder_manage(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    text, reply_markup = await render_folder_management_view(callback.from_user.id)
    await safe_edit_markdown(callback.message, text, reply_markup=reply_markup)
    await callback.answer()


@router.callback_query(F.data == "add_f_prompt")
async def process_add_folder_prompt(callback: CallbackQuery, state: FSMContext):
    await state.set_state(FolderAddForm.waiting_for_name)
    text = (
        "➕ **Создание нового раздела:**\n\n"
        "Напишите название раздела (например: `Работа`, `Личное`, `Идеи`, `Покупки`).\n\n"
        "_(нажмите кнопку ниже или отправьте /cancel для отмены)_"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="show_folders")]
    ])
    await safe_edit_markdown(callback.message, text, reply_markup=keyboard)
    await callback.answer()


@router.message(Command("cancel"), FolderAddForm.waiting_for_name)
@router.message(F.text.in_({"cancel", "/cancel", "отмена", "Отмена", "❌ Отмена", "🔙 Отмена"}), FolderAddForm.waiting_for_name)
async def process_cancel_add_folder(message: Message, state: FSMContext):
    await state.clear()
    text, reply_markup = await render_folders_view(message.from_user.id)
    await safe_send_markdown(message, "❌ Создание раздела отменено.", reply_markup=reply_markup)


@router.message(FolderAddForm.waiting_for_name)
async def process_save_folder_name(message: Message, state: FSMContext):
    name = message.text.strip() if message.text else ""
    if name.lower() in ["/cancel", "cancel", "отмена", "❌ отмена", "🔙 отмена"]:
        await state.clear()
        text, reply_markup = await render_folders_view(message.from_user.id)
        await safe_send_markdown(message, "❌ Создание раздела отменено.", reply_markup=reply_markup)
        return

    await state.clear()
    if name:
        folder_id = await notes_service.create_folder(message.from_user.id, name)
        await message.answer(f"📁 **Раздел «{name}» успешно создан!**")

    text, reply_markup = await render_folders_view(message.from_user.id)
    await safe_send_markdown(message, text, reply_markup=reply_markup)


# --- FOLDER DELETION HANDLERS WITH CONFIRMATION DIALOG ---

@router.callback_query(F.data.startswith("del_f_prompt:"))
async def process_del_folder_prompt(callback: CallbackQuery):
    folder_id = int(callback.data.split(":")[1])
    folder = await notes_service.get_folder_by_id(folder_id, callback.from_user.id)

    if not folder:
        await callback.answer("⚠️ Раздел не найден.", show_alert=True)
        return

    note_count = folder["note_count"]
    if note_count == 0:
        await notes_service.delete_folder(folder_id, callback.from_user.id, delete_contained_notes=False)
        await callback.answer("Раздел удалён 🗑")
        text, reply_markup = await render_folder_management_view(callback.from_user.id)
        await safe_edit_markdown(callback.message, text, reply_markup=reply_markup)
        return

    # Folder contains notes -> ask user option
    text = (
        f"⚠️ **В разделе «{folder['name']}» содержится {note_count} заметок.**\n\n"
        f"Удалить вложенные заметки вместе с разделом?"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Да, удалить всё", callback_data=f"del_f_confirm:{folder_id}:yes")],
        [InlineKeyboardButton(text="📦 Нет, перенести в «Без раздела»", callback_data=f"del_f_confirm:{folder_id}:no")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="f_manage")]
    ])
    await safe_edit_markdown(callback.message, text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("del_f_confirm:"))
async def process_del_folder_confirm(callback: CallbackQuery):
    parts = callback.data.split(":")
    folder_id = int(parts[1])
    delete_notes = (parts[2] == "yes")

    folder = await notes_service.get_folder_by_id(folder_id, callback.from_user.id)
    folder_name = folder["name"] if folder else "Раздел"

    success = await notes_service.delete_folder(folder_id, callback.from_user.id, delete_contained_notes=delete_notes)

    if success:
        if delete_notes:
            await callback.answer(f"Раздел «{folder_name}» и вложенные заметки удалены 🗑")
        else:
            await callback.answer(f"Раздел «{folder_name}» удалён. Заметки перенесены в «Без раздела» 📦")
    else:
        await callback.answer("Не удалось удалить раздел.")

    text, reply_markup = await render_folder_management_view(callback.from_user.id)
    await safe_edit_markdown(callback.message, text, reply_markup=reply_markup)


# --- NOTE ADDITION HANDLERS ---

@router.callback_query(F.data.startswith("add_n_prompt:"))
async def process_add_note_prompt(callback: CallbackQuery, state: FSMContext):
    folder_id = int(callback.data.split(":")[1])
    await state.set_state(NoteAddForm.waiting_for_content)
    await state.update_data(folder_id=folder_id)

    folder_info = ""
    if folder_id > 0:
        folder = await notes_service.get_folder_by_id(folder_id, callback.from_user.id)
        if folder:
            folder_info = f" в раздел **«{folder['name']}»**"

    text = (
        f"📝 **Новая заметка{folder_info}:**\n\n"
        f"Отправьте текст заметки одним сообщением.\n\n"
        f"_(нажмите кнопку ниже или отправьте /cancel для отмены)_"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"n_page:{folder_id}:1")]
    ])
    await safe_edit_markdown(callback.message, text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("set_n_folder:"))
async def process_set_note_folder(callback: CallbackQuery):
    parts = callback.data.split(":")
    note_id = int(parts[1])
    target_folder_id = int(parts[2])

    actual_target = target_folder_id if target_folder_id > 0 else None
    success = await notes_service.move_note_to_folder(note_id, callback.from_user.id, actual_target)

    target_name = "Без раздела"
    if target_folder_id > 0:
        f_info = await notes_service.get_folder_by_id(target_folder_id, callback.from_user.id)
        if f_info:
            target_name = f_info["name"]

    if success:
        await callback.answer(f"Заметка помещена в раздел «{target_name}» 📦")
    else:
        await callback.answer("Заметка осталась без раздела.")

    text, reply_markup = await render_folders_view(callback.from_user.id)
    await safe_edit_markdown(callback.message, text, reply_markup=reply_markup)


@router.message(Command("cancel"), NoteAddForm.waiting_for_content)
@router.message(F.text.in_({"cancel", "/cancel", "отмена", "Отмена", "❌ Отмена", "🔙 Отмена"}), NoteAddForm.waiting_for_content)
async def process_cancel_add_note(message: Message, state: FSMContext):
    data = await state.get_data()
    folder_id = data.get("folder_id", 0)
    await state.clear()
    text, reply_markup = await render_notes_view(message.from_user.id, folder_id=folder_id, page=1)
    await safe_send_markdown(message, "❌ Добавление заметки отменено.", reply_markup=reply_markup)


@router.message(NoteAddForm.waiting_for_content)
async def process_save_note_content(message: Message, state: FSMContext):
    data = await state.get_data()
    folder_id = data.get("folder_id", 0)
    content = message.text.strip() if message.text else ""

    if content.lower() in ["/cancel", "cancel", "отмена", "❌ отмена", "🔙 отмена"]:
        await state.clear()
        text, reply_markup = await render_notes_view(message.from_user.id, folder_id=folder_id, page=1)
        await safe_send_markdown(message, "❌ Добавление заметки отменено.", reply_markup=reply_markup)
        return

    await state.clear()
    if content:
        actual_folder = folder_id if folder_id > 0 else None
        note_id = await notes_service.add_note(message.from_user.id, content, folder_id=actual_folder)

        if folder_id == 0:
            folders = await notes_service.get_folders(message.from_user.id)
            if folders:
                buttons = []
                for f in folders:
                    buttons.append([InlineKeyboardButton(text=f"📁 {f['name']}", callback_data=f"set_n_folder:{note_id}:{f['id']}")])
                buttons.append([InlineKeyboardButton(text="📥 Оставить без раздела", callback_data=f"set_n_folder:{note_id}:0")])
                keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
                
                text = (
                    f"📌 Заметка **«{content}»** сохранена!\n\n"
                    f"📂 **В какой раздел её поместить?**"
                )
                await safe_send_markdown(message, text, reply_markup=keyboard)
                return

        await message.answer(f"📌 **Заметка сохранена!**\n_«{content}»_")

    text, reply_markup = await render_notes_view(message.from_user.id, folder_id=folder_id, page=1)
    await safe_send_markdown(message, text, reply_markup=reply_markup)


# --- NOTE EDITING HANDLERS ---

@router.callback_query(F.data.startswith("edit_n_prompt:"))
async def process_edit_note_prompt(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split(":")
    note_id = int(parts[1])
    folder_id = int(parts[2]) if len(parts) > 2 else 0
    page = int(parts[3]) if len(parts) > 3 else 1
    idx = int(parts[4]) if len(parts) > 4 else 1

    note = await notes_service.get_note_by_id(note_id, callback.from_user.id)
    if not note:
        await callback.answer("⚠️ Заметка не найдена.", show_alert=True)
        return

    await state.set_state(NoteEditForm.waiting_for_new_text)
    await state.update_data(note_id=note_id, folder_id=folder_id, page=page, idx=idx)

    copyable_content = format_copyable_text(note['content'])
    text = (
        f"✏️ **Редактирование заметки #{idx}:**\n\n"
        f"Текущий текст _(нажмите, чтобы скопировать)_:\n{copyable_content}\n\n"
        f"Отправьте новый текст заметки одним сообщением.\n\n"
        f"_(нажмите кнопку ниже или отправьте /cancel для отмены)_"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"select_n:{note_id}:{folder_id}:{page}:{idx}")]
    ])
    await safe_edit_markdown(callback.message, text, reply_markup=keyboard)
    await callback.answer()


@router.message(Command("cancel"), NoteEditForm.waiting_for_new_text)
@router.message(F.text.in_({"cancel", "/cancel", "отмена", "Отмена", "❌ Отмена", "🔙 Отмена"}), NoteEditForm.waiting_for_new_text)
async def process_cancel_edit_note(message: Message, state: FSMContext):
    data = await state.get_data()
    note_id = data.get("note_id")
    folder_id = data.get("folder_id", 0)
    page = data.get("page", 1)
    idx = data.get("idx", 1)
    await state.clear()

    if note_id:
        text, reply_markup = await render_note_detail_view(message.from_user.id, note_id, folder_id, page, idx)
        await safe_send_markdown(message, "❌ Редактирование заметки отменено.", reply_markup=reply_markup)
    else:
        text, reply_markup = await render_notes_view(message.from_user.id, folder_id=folder_id, page=page)
        await safe_send_markdown(message, text, reply_markup=reply_markup)


@router.message(NoteEditForm.waiting_for_new_text)
async def process_save_edited_note(message: Message, state: FSMContext):
    data = await state.get_data()
    note_id = data.get("note_id")
    folder_id = data.get("folder_id", 0)
    page = data.get("page", 1)
    idx = data.get("idx", 1)
    new_text = message.text.strip() if message.text else ""

    if new_text.lower() in ["/cancel", "cancel", "отмена", "❌ отмена", "🔙 отмена"]:
        await state.clear()
        text, reply_markup = await render_note_detail_view(message.from_user.id, note_id, folder_id, page, idx)
        await safe_send_markdown(message, "❌ Редактирование отменено.", reply_markup=reply_markup)
        return

    await state.clear()
    if note_id and new_text:
        await notes_service.update_note(note_id, message.from_user.id, new_text)
        await message.answer(f"✨ **Заметка #{idx} обновлена!**")

    text, reply_markup = await render_note_detail_view(message.from_user.id, note_id, folder_id, page, idx)
    await safe_send_markdown(message, text, reply_markup=reply_markup)


# --- CLEAR NOTES HANDLERS ---

@router.callback_query(F.data == "clear_notes_prompt")
async def process_clear_notes_prompt(callback: CallbackQuery):
    text = "⚠️ **Вы уверены, что хотите удалить ВСЕ сохранённые заметки во всех разделах?**\nЭто действие нельзя отменить!"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Да, очистить все", callback_data="confirm_clear_notes")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="show_folders")]
    ])
    await safe_edit_markdown(callback.message, text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("clear_folder_prompt:"))
async def process_clear_folder_prompt(callback: CallbackQuery):
    folder_id = int(callback.data.split(":")[1])
    folder_name = "Без раздела"
    if folder_id > 0:
        folder = await notes_service.get_folder_by_id(folder_id, callback.from_user.id)
        if folder:
            folder_name = f"«{folder['name']}»"

    text = f"⚠️ **Вы уверены, что хотите удалить все заметки в разделе {folder_name}?**"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Да, очистить этот раздел", callback_data=f"confirm_clear_folder:{folder_id}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"open_f:{folder_id}")]
    ])
    await safe_edit_markdown(callback.message, text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data == "confirm_clear_notes")
async def process_confirm_clear_notes(callback: CallbackQuery):
    await notes_service.clear_notes(callback.from_user.id)
    await callback.answer("Все заметки очищены 🧹")
    text, reply_markup = await render_folders_view(callback.from_user.id)
    await safe_edit_markdown(callback.message, text, reply_markup=reply_markup)


@router.callback_query(F.data.startswith("confirm_clear_folder:"))
async def process_confirm_clear_folder(callback: CallbackQuery):
    folder_id = int(callback.data.split(":")[1])
    if folder_id == 0:
        await notes_service.clear_notes(callback.from_user.id, unsorted_only=True)
    elif folder_id > 0:
        await notes_service.clear_notes(callback.from_user.id, folder_id=folder_id)
    else:
        await notes_service.clear_notes(callback.from_user.id)

    await callback.answer("Заметки раздела очищены 🧹")
    text, reply_markup = await render_notes_view(callback.from_user.id, folder_id=folder_id, page=1)
    await safe_edit_markdown(callback.message, text, reply_markup=reply_markup)


# --- MOVE NOTE TO FOLDER HANDLERS ---

@router.callback_query(F.data.startswith("move_n_prompt:"))
async def process_move_note_prompt(callback: CallbackQuery):
    parts = callback.data.split(":")
    note_id = int(parts[1])
    current_folder_id = int(parts[2]) if len(parts) > 2 else 0
    page = int(parts[3]) if len(parts) > 3 else 1
    idx = int(parts[4]) if len(parts) > 4 else 1

    note = await notes_service.get_note_by_id(note_id, callback.from_user.id)
    if not note:
        await callback.answer("⚠️ Заметка не найдена.", show_alert=True)
        return

    folders = await notes_service.get_folders(callback.from_user.id)

    text = (
        f"📂 **Перемещение заметки #{idx} в другой раздел:**\n\n"
        f"Заметка: **«{note['content']}»**\n\n"
        f"Выберите целевой раздел:"
    )

    buttons = []
    for f in folders:
        f_id = f["id"]
        if note.get("folder_id") == f_id:
            continue
        buttons.append([InlineKeyboardButton(text=f"📁 {f['name']}", callback_data=f"move_n_confirm:{note_id}:{f_id}:{current_folder_id}:{page}:{idx}")])

    if note.get("folder_id") is not None:
        buttons.append([InlineKeyboardButton(text="📥 Перенести в «Без раздела»", callback_data=f"move_n_confirm:{note_id}:0:{current_folder_id}:{page}:{idx}")])

    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data=f"select_n:{note_id}:{current_folder_id}:{page}:{idx}")])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await safe_edit_markdown(callback.message, text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("move_n_confirm:"))
async def process_move_note_confirm(callback: CallbackQuery):
    parts = callback.data.split(":")
    note_id = int(parts[1])
    target_folder_id = int(parts[2])
    current_folder_id = int(parts[3]) if len(parts) > 3 else 0
    page = int(parts[4]) if len(parts) > 4 else 1
    idx = int(parts[5]) if len(parts) > 5 else 1

    actual_target = target_folder_id if target_folder_id > 0 else None
    success = await notes_service.move_note_to_folder(note_id, callback.from_user.id, actual_target)

    target_name = "Без раздела"
    if target_folder_id > 0:
        f_info = await notes_service.get_folder_by_id(target_folder_id, callback.from_user.id)
        if f_info:
            target_name = f_info["name"]

    if success:
        await callback.answer(f"Заметка перенесена в «{target_name}» 📦")
    else:
        await callback.answer("⚠️ Не удалось переместить заметку.")

    text, reply_markup = await render_note_detail_view(callback.from_user.id, note_id, current_folder_id, page, idx)
    await safe_edit_markdown(callback.message, text, reply_markup=reply_markup)
