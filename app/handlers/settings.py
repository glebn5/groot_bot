import os
import re
import logging
from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from app.config import settings
from app.services.obsidian import obsidian_service
from app.services.llm import llm_service
from app.services.calendar import calendar_service

logger = logging.getLogger(__name__)
router = Router(name="settings")


class SettingsForm(StatesGroup):
    waiting_for_value = State()
    waiting_for_sa_file = State()


def update_env_file(key: str, value: str, env_path: str = ".env"):
    """
    Updates or adds key=value pair in .env file.
    """
    lines = []
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

    key_found = False
    new_lines = []
    for line in lines:
        if line.strip().startswith(f"{key}="):
            new_lines.append(f"{key}={value}\n")
            key_found = True
        else:
            new_lines.append(line)

    if not key_found:
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines.append("\n")
        new_lines.append(f"{key}={value}\n")

    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)


def build_settings_keyboard() -> InlineKeyboardMarkup:
    # Check statuses
    groq_ok = bool(settings.GROQ_API_KEY and not settings.GROQ_API_KEY.startswith("gsk_your_"))
    webdav_login_ok = bool(settings.WEBDAV_LOGIN and not settings.WEBDAV_LOGIN.startswith("your_"))
    webdav_pwd_ok = bool(settings.WEBDAV_PASSWORD and not settings.WEBDAV_PASSWORD.startswith("your_"))
    gemini_ok = llm_service._is_valid_gemini_key()
    calendar_ok = calendar_service.is_configured()

    groq_icon = "🟢" if groq_ok else "🔴"
    webdav_login_icon = "🟢" if webdav_login_ok else "🔴"
    webdav_pwd_icon = "🟢" if webdav_pwd_ok else "🔴"
    gemini_icon = "🟢" if gemini_ok else "⚪"
    calendar_icon = "🟢" if calendar_ok else "🔴"

    keyboard = [
        [
            InlineKeyboardButton(text=f"{groq_icon} GROQ API Key", callback_data="set_key:GROQ_API_KEY")
        ],
        [
            InlineKeyboardButton(text=f"{webdav_login_icon} WebDAV Логин", callback_data="set_key:WEBDAV_LOGIN"),
            InlineKeyboardButton(text=f"{webdav_pwd_icon} WebDAV Пароль", callback_data="set_key:WEBDAV_PASSWORD")
        ],
        [
            InlineKeyboardButton(text=f"📂 Vault Path: {settings.WEBDAV_VAULT_PATH}", callback_data="set_key:WEBDAV_VAULT_PATH")
        ],
        [
            InlineKeyboardButton(text=f"{calendar_icon} Google Calendar ID ({settings.GOOGLE_CALENDAR_ID})", callback_data="set_key:GOOGLE_CALENDAR_ID")
        ],
        [
            InlineKeyboardButton(text=f"🔑 Google Service Account JSON", callback_data="upload_sa_json")
        ],
        [
            InlineKeyboardButton(text=f"{gemini_icon} Gemini API Key (опционально)", callback_data="set_key:GEMINI_API_KEY")
        ],
        [
            InlineKeyboardButton(text="❌ Закрыть", callback_data="close_settings")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


@router.message(Command("settings"))
async def cmd_settings(message: Message, state: FSMContext):
    await state.clear()
    text = (
        "⚙️ **Панель управления настройками Groot**\n\n"
        "Вы можете изменить ключи API и доступы прямо отсюда.\n"
        "Нажмите на соответствующую кнопку для ввода нового значения:"
    )
    await message.answer(text, reply_markup=build_settings_keyboard(), parse_mode="Markdown")


@router.callback_query(F.data.startswith("set_key:"))
async def process_set_key_callback(callback: CallbackQuery, state: FSMContext):
    key_name = callback.data.split(":", 1)[1]
    await state.update_data(target_key=key_name)
    await state.set_state(SettingsForm.waiting_for_value)

    prompt_msg = (
        f"✍️ **Введите новое значение для `{key_name}`:**\n\n"
        f"_(Отправьте текстом новое значение или наберите `/cancel` для отмены)_"
    )
    await callback.message.edit_text(prompt_msg, parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data == "upload_sa_json")
async def process_upload_sa_json(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SettingsForm.waiting_for_sa_file)
    prompt_msg = (
        "📁 **Загрузка Google Service Account Key (.json)**\n\n"
        "Пришлите файл `service_account.json` документом в этот чат или вставьте его содержимое текстом.\n"
        "_(Наберите `/cancel` для отмены)_"
    )
    await callback.message.edit_text(prompt_msg, parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data == "close_settings")
async def process_close_settings(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.delete()
    await callback.answer("Настройки закрыты.")


@router.message(Command("cancel"), FSMContext)
async def cmd_cancel_settings(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Ввод отменен.", reply_markup=build_settings_keyboard())


@router.message(SettingsForm.waiting_for_sa_file)
async def process_sa_file(message: Message, bot: Bot, state: FSMContext):
    sa_path = settings.GOOGLE_SERVICE_ACCOUNT_FILE
    sa_dir = os.path.dirname(sa_path)
    if sa_dir and not os.path.exists(sa_dir):
        os.makedirs(sa_dir, exist_ok=True)

    if message.document:
        # Download document
        file = await bot.get_file(message.document.file_id)
        await bot.download_file(file.file_path, destination=sa_path)
    elif message.text:
        # Save text JSON
        with open(sa_path, "w", encoding="utf-8") as f:
            f.write(message.text)
    else:
        await message.answer("⚠️ Пожалуйста, прикрепите .json файл или отправьте его текст.")
        return

    # Re-initialize Calendar service
    calendar_service._init_service()
    await state.clear()

    if calendar_service.is_configured():
        await message.answer("✅ Файл `service_account.json` успешно сохранен! Google Календарь подключен.", reply_markup=build_settings_keyboard(), parse_mode="Markdown")
    else:
        await message.answer("⚠️ Файл сохранен, но инициализация Календаря не удалась. Проверьте содержимое JSON.", reply_markup=build_settings_keyboard(), parse_mode="Markdown")


@router.message(SettingsForm.waiting_for_value)
async def process_new_setting_value(message: Message, state: FSMContext):
    data = await state.get_data()
    key_name = data.get("target_key")
    new_value = message.text.strip() if message.text else ""

    if not key_name:
        await state.clear()
        return

    # Update .env
    update_env_file(key_name, new_value)

    # Update in-memory settings
    setattr(settings, key_name, new_value)

    # Re-initialize services if needed
    if key_name in ["WEBDAV_LOGIN", "WEBDAV_PASSWORD", "WEBDAV_HOSTNAME", "WEBDAV_VAULT_PATH"]:
        obsidian_service.__init__()
    elif key_name in ["GROQ_API_KEY", "GEMINI_API_KEY"]:
        llm_service.__init__()
    elif key_name == "GOOGLE_CALENDAR_ID":
        calendar_service._init_service()

    await state.clear()
    success_msg = f"✅ Переменная **`{key_name}`** успешно обновлена!"
    await message.answer(success_msg, reply_markup=build_settings_keyboard(), parse_mode="Markdown")
