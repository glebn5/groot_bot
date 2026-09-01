import os
import re
import logging
from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from typing import Union
from app.config import settings
from app.services.obsidian import obsidian_service
from app.services.llm import llm_service
from app.services.calendar import calendar_service
from app.services.scheduler import scheduler_service
from app.services.goals import goals_service
from app.utils.timezone import get_today
from app.handlers.text import render_schedule_view

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


def get_statuses():
    groq_ok = bool(settings.GROQ_API_KEY and not settings.GROQ_API_KEY.startswith("gsk_your_"))
    webdav_login_ok = bool(settings.WEBDAV_LOGIN and not settings.WEBDAV_LOGIN.startswith("your_"))
    webdav_pwd_ok = bool(settings.WEBDAV_PASSWORD and not settings.WEBDAV_PASSWORD.startswith("your_"))
    webdav_ok = webdav_login_ok and webdav_pwd_ok and obsidian_service.is_configured()
    gemini_ok = llm_service._is_valid_gemini_key()
    calendar_ok = calendar_service.is_configured()
    snooze_min = getattr(settings, "DEFAULT_SNOOZE_MINUTES", 5)
    return {
        "groq_ok": groq_ok,
        "webdav_ok": webdav_ok,
        "webdav_login_ok": webdav_login_ok,
        "webdav_pwd_ok": webdav_pwd_ok,
        "gemini_ok": gemini_ok,
        "calendar_ok": calendar_ok,
        "snooze_min": snooze_min
    }


def render_settings_main():
    st = get_statuses()
    groq_ic = "🟢 Подключен" if st["groq_ok"] else "🔴 Не настроен"
    gemini_ic = "🟢 Подключен" if st["gemini_ok"] else "⚪ Не подключен"
    webdav_ic = "🟢 Подключен" if st["webdav_ok"] else "🔴 Не настроен"
    cal_ic = "🟢 Подключен" if st["calendar_ok"] else "🔴 Не подключен"

    text = (
        "⚙️ **Центр управления настройками Groot**\n\n"
        "📊 **Текущий статус интеграций:**\n"
        f"• 🤖 **GROQ API:** {groq_ic}\n"
        f"• 💎 **Gemini API:** {gemini_ic}\n"
        f"• 📂 **Obsidian Vault:** {webdav_ic} (`{settings.WEBDAV_VAULT_PATH}`)\n"
        f"• 📅 **Google Календарь:** {cal_ic}\n"
        f"• ⏱ **Откладывание по умолч.:** `{st['snooze_min']} мин`\n\n"
        "Выберите нужный раздел для настройки:"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🤖 ИИ и Нейросети (GROQ / Gemini)", callback_data="settings_cat:llm")],
        [InlineKeyboardButton(text="📂 Obsidian & Vault (WebDAV)", callback_data="settings_cat:obsidian")],
        [InlineKeyboardButton(text="📅 Google Календарь", callback_data="settings_cat:calendar")],
        [InlineKeyboardButton(text="⏱ Напоминания и Таймеры", callback_data="settings_cat:snooze")],
        [InlineKeyboardButton(text="❌ Закрыть", callback_data="close_settings")]
    ])
    return text, keyboard


def render_settings_llm():
    st = get_statuses()
    groq_ic = "🟢 Подключен" if st["groq_ok"] else "🔴 Не настроен"
    gemini_ic = "🟢 Подключен" if st["gemini_ok"] else "⚪ Не подключен (опционально)"

    text = (
        "🤖 **Настройки ИИ & Нейросетей**\n\n"
        "Управление API ключами для генерации ответов и распознавания картинок:\n\n"
        f"• **GROQ API Key:** {groq_ic}\n"
        f"• **Gemini API Key:** {gemini_ic}"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔑 Изменить GROQ API Key", callback_data="set_key:GROQ_API_KEY:llm")],
        [InlineKeyboardButton(text="💎 Изменить Gemini API Key", callback_data="set_key:GEMINI_API_KEY:llm")],
        [InlineKeyboardButton(text="🔙 Назад в настройки", callback_data="settings_cat:main")]
    ])
    return text, keyboard


def render_settings_obsidian():
    st = get_statuses()
    login_ic = "🟢" if st["webdav_login_ok"] else "🔴"
    pwd_ic = "🟢" if st["webdav_pwd_ok"] else "🔴"

    text = (
        "📂 **Синхронизация Obsidian Vault (WebDAV)**\n\n"
        "Настройка подключения к вашему облачному хранилищу Obsidian:\n\n"
        f"• **WebDAV Server:** `{settings.WEBDAV_HOSTNAME}`\n"
        f"• **Логин:** {login_ic} `{settings.WEBDAV_LOGIN or 'не задан'}`\n"
        f"• **Пароль:** {pwd_ic} `{'••••••••' if settings.WEBDAV_PASSWORD else 'не задан'}`\n"
        f"• **Путь к Vault:** `{settings.WEBDAV_VAULT_PATH}`"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=f"{login_ic} WebDAV Логин", callback_data="set_key:WEBDAV_LOGIN:obsidian"),
            InlineKeyboardButton(text=f"{pwd_ic} WebDAV Пароль", callback_data="set_key:WEBDAV_PASSWORD:obsidian")
        ],
        [InlineKeyboardButton(text=f"📂 Изменить Vault Path ({settings.WEBDAV_VAULT_PATH})", callback_data="set_key:WEBDAV_VAULT_PATH:obsidian")],
        [InlineKeyboardButton(text="🔙 Назад в настройки", callback_data="settings_cat:main")]
    ])
    return text, keyboard


def render_settings_calendar():
    st = get_statuses()
    cal_ic = "🟢 Подключен" if st["calendar_ok"] else "🔴 Не настроен"

    text = (
        "📅 **Интеграция с Google Календарём**\n\n"
        "Настройки синхронизации событий и встреч:\n\n"
        f"• **Статус подключения:** {cal_ic}\n"
        f"• **Google Calendar ID:** `{settings.GOOGLE_CALENDAR_ID}`\n"
        f"• **Файл авторизации:** `{settings.GOOGLE_SERVICE_ACCOUNT_FILE}`"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📆 Calendar ID ({settings.GOOGLE_CALENDAR_ID})", callback_data="set_key:GOOGLE_CALENDAR_ID:calendar")],
        [InlineKeyboardButton(text="🔑 Загрузить Service Account JSON", callback_data="upload_sa_json")],
        [InlineKeyboardButton(text="🔙 Назад в настройки", callback_data="settings_cat:main")]
    ])
    return text, keyboard


def render_settings_snooze():
    st = get_statuses()
    sched_interval = getattr(settings, "SCHEDULE_SUMMARY_INTERVAL_HOURS", 0)
    sched_status = f"Каждые {sched_interval} ч" if sched_interval > 0 else "Отключено"
    allow_night = getattr(settings, "ALLOW_NIGHT_NOTIFICATIONS", False)
    quiet_start = getattr(settings, "QUIET_HOURS_START", "23:00")
    quiet_end = getattr(settings, "QUIET_HOURS_END", "08:00")

    night_status = "🟢 24/7 (Разрешены)" if allow_night else f"🔴 Часы тишины ({quiet_start}–{quiet_end})"

    text = (
        "⏱ **Настройки напоминаний и таймеров**\n\n"
        "Управление временем откладывания, сводками, целями и ночным режимом:\n\n"
        f"• **Базовое откладывание:** `{st['snooze_min']} мин`\n"
        f"• **Авто-напоминание планов на день:** `{sched_status}`\n"
        f"• **Ночные уведомления:** `{night_status}`\n"
        "• **Напоминания для задач без времени:** `08:00, 12:00, 15:00, 19:00`"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎯 Цели на месяц", callback_data="settings_cat:goals")],
        [InlineKeyboardButton(text=f"🌙 Ночной режим ({'24/7' if allow_night else f'Не беспокоить {quiet_start}-{quiet_end}'})", callback_data="settings_cat:quiet_hours")],
        [InlineKeyboardButton(text=f"⏱ Время откладывания ({st['snooze_min']} мин)", callback_data="set_key:DEFAULT_SNOOZE_MINUTES:snooze")],
        [InlineKeyboardButton(text=f"🔔 Периодичность планов ({sched_status})", callback_data="settings_cat:sched_summary")],
        [InlineKeyboardButton(text="🔙 Назад в настройки", callback_data="settings_cat:main")]
    ])
    return text, keyboard


def render_settings_sched_summary():
    sched_interval = getattr(settings, "SCHEDULE_SUMMARY_INTERVAL_HOURS", 0)
    sched_status = f"Каждые {sched_interval} ч" if sched_interval > 0 else "Отключено"

    text = (
        "🔔 **Периодическое напоминание планов на день**\n\n"
        f"Текущая периодичность: **{sched_status}**\n\n"
        "Выберите интервал отправки сводки ваших задач и планов:"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏱ Каждые 2 часа", callback_data="set_sched_sum:2")],
        [InlineKeyboardButton(text="⏱ Каждые 3 часа", callback_data="set_sched_sum:3")],
        [InlineKeyboardButton(text="⏱ Каждые 4 часа", callback_data="set_sched_sum:4")],
        [InlineKeyboardButton(text="✍️ Указать свой интервал (в часах)", callback_data="set_key:SCHEDULE_SUMMARY_INTERVAL_HOURS:snooze")],
        [InlineKeyboardButton(text="❌ Отключить", callback_data="set_sched_sum:0")],
        [InlineKeyboardButton(text="🔙 Назад в Напоминания", callback_data="settings_cat:snooze")]
    ])
    return text, keyboard


DAY_NAMES_RU = {
    "mon": "Пн", "tue": "Вт", "wed": "Ср", "thu": "Чт",
    "fri": "Пт", "sat": "Сб", "sun": "Вс"
}


async def render_settings_goals(user_id: int):
    st = await goals_service.get_goal_settings(user_id)
    is_enabled = bool(st["is_enabled"])
    status_str = "🟢 Включено" if is_enabled else "🔴 Отключено"

    raw_days = st["days_of_week"].split(",") if st["days_of_week"] else []
    days_formatted = ", ".join([DAY_NAMES_RU.get(d.strip(), d.strip()) for d in raw_days if d.strip()]) or "Не выбраны"
    time_str = st["time_str"]

    text = (
        "🎯 **Настройки напоминаний целей на месяц**\n\n"
        f"• **Статус рассылки:** {status_str}\n"
        f"• **Дни недели:** `{days_formatted}`\n"
        f"• **Время рассылки:** `{time_str}`\n\n"
        "Выберите элемент для изменения:"
    )

    toggle_btn_text = "🔴 Отключить рассылку" if is_enabled else "🟢 Включить рассылку"
    toggle_val = "0" if is_enabled else "1"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=toggle_btn_text, callback_data=f"toggle_g_rem:{toggle_val}")],
        [InlineKeyboardButton(text=f"📅 Дни недели ({days_formatted})", callback_data="settings_cat:goals_days")],
        [InlineKeyboardButton(text=f"⏰ Время рассылки ({time_str})", callback_data="settings_cat:goals_time")],
        [InlineKeyboardButton(text="🔙 Назад в Напоминания", callback_data="settings_cat:snooze")]
    ])
    return text, keyboard


async def render_settings_goals_days(user_id: int):
    st = await goals_service.get_goal_settings(user_id)
    current_days = [d.strip() for d in st["days_of_week"].split(",") if d.strip()]

    days_order = [("mon", "Пн"), ("tue", "Вт"), ("wed", "Ср"), ("thu", "Чт"), ("fri", "Пт"), ("sat", "Сб"), ("sun", "Вс")]

    text = (
        "📅 **Выбор дней рассылки целей**\n\n"
        "Нажимайте на дни недели для включения или отключения:"
    )

    buttons = []
    row1 = []
    row2 = []
    for code, name in days_order[:4]:
        check = "✅" if code in current_days else "⬜"
        row1.append(InlineKeyboardButton(text=f"{check} {name}", callback_data=f"toggle_g_day:{code}"))
    for code, name in days_order[4:]:
        check = "✅" if code in current_days else "⬜"
        row2.append(InlineKeyboardButton(text=f"{check} {name}", callback_data=f"toggle_g_day:{code}"))

    buttons.append(row1)
    buttons.append(row2)
    buttons.append([InlineKeyboardButton(text="🔙 Назад к настройкам целей", callback_data="settings_cat:goals")])

    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


async def render_settings_goals_time(user_id: int):
    st = await goals_service.get_goal_settings(user_id)
    current_time = st["time_str"]

    text = (
        "⏰ **Выбор времени рассылки целей**\n\n"
        f"Текущее время: **{current_time}**\n"
        "Выберите удобное время:"
    )

    times = ["08:00", "09:00", "10:00", "12:00", "18:00", "20:00"]
    buttons = []
    row = []
    for t_str in times:
        check = "✅ " if t_str == current_time else ""
        row.append(InlineKeyboardButton(text=f"{check}{t_str}", callback_data=f"set_g_time:{t_str}"))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([InlineKeyboardButton(text="🔙 Назад к настройкам целей", callback_data="settings_cat:goals")])

    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


def render_settings_quiet_hours():
    allow_night = getattr(settings, "ALLOW_NIGHT_NOTIFICATIONS", False)
    quiet_start = getattr(settings, "QUIET_HOURS_START", "23:00")
    quiet_end = getattr(settings, "QUIET_HOURS_END", "08:00")

    status_str = "🟢 Включены 24/7 (приходят и ночью)" if allow_night else f"🔴 Часы тишины ({quiet_start}–{quiet_end})"

    text = (
        "🌙 **Настройка Ночного Режима (Часы Тишины)**\n\n"
        f"• **Статус ночных рассылок:** {status_str}\n"
        f"• **Интервал тишины:** с `{quiet_start}` до `{quiet_end}`\n\n"
        "_(При ваших личных обращениях бот всегда отвечает мгновенно! Часы тишины действуют только на фоновые автоматические рассылки)_"
    )

    toggle_btn_text = "🔴 Заблокировать ночью" if allow_night else "🟢 Разрешить 24/7"
    toggle_val = "0" if allow_night else "1"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=toggle_btn_text, callback_data=f"toggle_night_notif:{toggle_val}")],
        [InlineKeyboardButton(text=f"🌙 Начало тишины ({quiet_start})", callback_data="settings_cat:quiet_start")],
        [InlineKeyboardButton(text=f"☀️ Конец тишины ({quiet_end})", callback_data="settings_cat:quiet_end")],
        [InlineKeyboardButton(text="🔙 Назад в Напоминания", callback_data="settings_cat:snooze")]
    ])
    return text, keyboard


def render_settings_quiet_start():
    quiet_start = getattr(settings, "QUIET_HOURS_START", "23:00")
    text = (
        "🌙 **Выбор времени начала Часов Тишины**\n\n"
        f"Текущее время начала: **{quiet_start}**\n"
        "Выберите время, начиная с которого авто-уведомления не будут беспокоить:"
    )
    times = ["21:00", "22:00", "23:00", "00:00"]
    buttons = []
    row = []
    for t_str in times:
        check = "✅ " if t_str == quiet_start else ""
        row.append(InlineKeyboardButton(text=f"{check}{t_str}", callback_data=f"set_quiet_start:{t_str}"))
    buttons.append(row)
    buttons.append([InlineKeyboardButton(text="✍️ Указать своё время", callback_data="set_key:QUIET_HOURS_START:quiet_hours")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад в Ночной режим", callback_data="settings_cat:quiet_hours")])
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


def render_settings_quiet_end():
    quiet_end = getattr(settings, "QUIET_HOURS_END", "08:00")
    text = (
        "☀️ **Выбор времени окончания Часов Тишины**\n\n"
        f"Текущее время окончания: **{quiet_end}**\n"
        "Выберите время, до которого авто-уведомления не приходят:"
    )
    times = ["06:00", "07:00", "08:00", "09:00"]
    buttons = []
    row = []
    for t_str in times:
        check = "✅ " if t_str == quiet_end else ""
        row.append(InlineKeyboardButton(text=f"{check}{t_str}", callback_data=f"set_quiet_end:{t_str}"))
    buttons.append(row)
    buttons.append([InlineKeyboardButton(text="✍️ Указать своё время", callback_data="set_key:QUIET_HOURS_END:quiet_hours")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад в Ночной режим", callback_data="settings_cat:quiet_hours")])
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


@router.message(Command("settings"))
@router.message(F.text.in_({"⚙️ Настройки", "Настройки"}))
async def cmd_settings(message: Message, state: FSMContext):
    await state.clear()
    text, keyboard = render_settings_main()
    await message.answer(text, reply_markup=keyboard, parse_mode="Markdown")


@router.callback_query(F.data.startswith("settings_cat:"))
async def process_settings_category(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    cat = callback.data.split(":", 1)[1]
    if cat == "llm":
        text, keyboard = render_settings_llm()
    elif cat == "obsidian":
        text, keyboard = render_settings_obsidian()
    elif cat == "calendar":
        text, keyboard = render_settings_calendar()
    elif cat == "snooze":
        text, keyboard = render_settings_snooze()
    elif cat == "sched_summary":
        text, keyboard = render_settings_sched_summary()
    elif cat == "goals":
        text, keyboard = await render_settings_goals(callback.from_user.id)
    elif cat == "goals_days":
        text, keyboard = await render_settings_goals_days(callback.from_user.id)
    elif cat == "goals_time":
        text, keyboard = await render_settings_goals_time(callback.from_user.id)
    elif cat == "quiet_hours":
        text, keyboard = render_settings_quiet_hours()
    elif cat == "quiet_start":
        text, keyboard = render_settings_quiet_start()
    elif cat == "quiet_end":
        text, keyboard = render_settings_quiet_end()
    else:
        text, keyboard = render_settings_main()

    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    except Exception:
        await callback.message.answer(text, reply_markup=keyboard, parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data.startswith("set_sched_sum:"))
async def process_set_sched_sum_callback(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    hours_str = callback.data.split(":", 1)[1]
    try:
        hours = int(hours_str)
        update_env_file("SCHEDULE_SUMMARY_INTERVAL_HOURS", str(hours))
        settings.SCHEDULE_SUMMARY_INTERVAL_HOURS = hours
        scheduler_service.setup_periodic_schedule_summary(callback.from_user.id, hours)

        status_msg = f"Каждые {hours} ч" if hours > 0 else "Отключено"
        await callback.answer(f"Периодичность планов: {status_msg} ✨")
    except Exception as e:
        logger.error(f"Error setting schedule summary interval: {e}")
        await callback.answer("⚠️ Ошибка сохранения периодичности", show_alert=True)

    text, keyboard = render_settings_snooze()
    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    except Exception:
        await callback.message.answer(text, reply_markup=keyboard, parse_mode="Markdown")


@router.callback_query(F.data.startswith("set_key:"))
async def process_set_key_callback(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split(":")
    key_name = parts[1]
    category = parts[2] if len(parts) > 2 else "main"

    await state.update_data(target_key=key_name, target_cat=category)
    await state.set_state(SettingsForm.waiting_for_value)

    prompt_msg = (
        f"✍️ **Введите новое значение для `{key_name}`:**\n\n"
        f"_(Отправьте текстом новое значение в чат или наберите `/cancel` для отмены)_"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Отмена", callback_data=f"settings_cat:{category}")]
    ])
    try:
        await callback.message.edit_text(prompt_msg, reply_markup=keyboard, parse_mode="Markdown")
    except Exception:
        await callback.message.answer(prompt_msg, reply_markup=keyboard, parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data == "upload_sa_json")
async def process_upload_sa_json(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SettingsForm.waiting_for_sa_file)
    prompt_msg = (
        "📁 **Загрузка Google Service Account Key (.json)**\n\n"
        "Пришлите файл `service_account.json` документом в этот чат или вставьте его содержимое текстом.\n\n"
        "_(Наберите `/cancel` для отмены)_"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Отмена", callback_data="settings_cat:calendar")]
    ])
    try:
        await callback.message.edit_text(prompt_msg, reply_markup=keyboard, parse_mode="Markdown")
    except Exception:
        await callback.message.answer(prompt_msg, reply_markup=keyboard, parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data.startswith("toggle_g_rem:"))
async def process_toggle_goals_reminder(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    val_str = callback.data.split(":", 1)[1]
    is_enabled = bool(int(val_str))
    await goals_service.update_goal_settings(callback.from_user.id, is_enabled=is_enabled)

    status_msg = "включена 🟢" if is_enabled else "отключена 🔴"
    await callback.answer(f"Рассылка целей {status_msg}")

    text, keyboard = await render_settings_goals(callback.from_user.id)
    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    except Exception:
        await callback.message.answer(text, reply_markup=keyboard, parse_mode="Markdown")


@router.callback_query(F.data.startswith("toggle_g_day:"))
async def process_toggle_goals_day(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    day_code = callback.data.split(":", 1)[1]
    st = await goals_service.get_goal_settings(callback.from_user.id)
    current_days = [d.strip() for d in st["days_of_week"].split(",") if d.strip()]

    if day_code in current_days:
        current_days.remove(day_code)
    else:
        current_days.append(day_code)

    new_days_str = ",".join(current_days)
    await goals_service.update_goal_settings(callback.from_user.id, days_of_week=new_days_str)
    await callback.answer("Дни рассылки обновлены ✨")

    text, keyboard = await render_settings_goals_days(callback.from_user.id)
    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    except Exception:
        await callback.message.answer(text, reply_markup=keyboard, parse_mode="Markdown")


@router.callback_query(F.data.startswith("set_g_time:"))
async def process_set_goals_time(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    time_str = callback.data.split(":", 1)[1]
    await goals_service.update_goal_settings(callback.from_user.id, time_str=time_str)
    await callback.answer(f"Время рассылки установлено на {time_str} ⏰")

    text, keyboard = await render_settings_goals(callback.from_user.id)
    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    except Exception:
        await callback.message.answer(text, reply_markup=keyboard, parse_mode="Markdown")


@router.callback_query(F.data.startswith("toggle_night_notif:"))
async def process_toggle_night_notif(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    val_str = callback.data.split(":", 1)[1]
    is_allow = bool(int(val_str))
    update_env_file("ALLOW_NIGHT_NOTIFICATIONS", str(is_allow))
    settings.ALLOW_NIGHT_NOTIFICATIONS = is_allow

    status_msg = "разрешены 24/7 🟢" if is_allow else "заблокированы ночью 🔴"
    await callback.answer(f"Ночные уведомления {status_msg}")

    text, keyboard = render_settings_quiet_hours()
    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    except Exception:
        await callback.message.answer(text, reply_markup=keyboard, parse_mode="Markdown")


@router.callback_query(F.data.startswith("set_quiet_start:"))
async def process_set_quiet_start(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    t_str = callback.data.split(":", 1)[1]
    update_env_file("QUIET_HOURS_START", t_str)
    settings.QUIET_HOURS_START = t_str
    await callback.answer(f"Начало тишины установлено на {t_str} 🌙")

    text, keyboard = render_settings_quiet_hours()
    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    except Exception:
        await callback.message.answer(text, reply_markup=keyboard, parse_mode="Markdown")


@router.callback_query(F.data.startswith("set_quiet_end:"))
async def process_set_quiet_end(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    t_str = callback.data.split(":", 1)[1]
    update_env_file("QUIET_HOURS_END", t_str)
    settings.QUIET_HOURS_END = t_str
    await callback.answer(f"Конец тишины установлен на {t_str} ☀️")

    text, keyboard = render_settings_quiet_hours()
    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    except Exception:
        await callback.message.answer(text, reply_markup=keyboard, parse_mode="Markdown")


@router.message(Command("close"))
@router.callback_query(F.data == "close_settings")
async def process_close_settings(event: Union[Message, CallbackQuery], state: FSMContext):
    await state.clear()
    chat_id = event.from_user.id if isinstance(event, CallbackQuery) else event.chat.id
    today = get_today()
    sched_text, reply_markup = await render_schedule_view(chat_id, today)

    msg_text = f"🌴 **Настройки закрыты.**\n\n{sched_text}"

    if isinstance(event, CallbackQuery):
        try:
            await event.message.edit_text(msg_text, reply_markup=reply_markup, parse_mode="Markdown")
        except Exception:
            await event.message.answer(msg_text, reply_markup=reply_markup, parse_mode="Markdown")
        await event.answer("Настройки закрыты.")
    else:
        await event.answer(msg_text, reply_markup=reply_markup, parse_mode="Markdown")


@router.message(Command("cancel"), FSMContext)
async def cmd_cancel_settings(message: Message, state: FSMContext):
    data = await state.get_data()
    cat = data.get("target_cat", "main")
    await state.clear()
    text, keyboard = render_settings_main()
    await message.answer("❌ Ввод отменен.", reply_markup=keyboard)


@router.message(SettingsForm.waiting_for_sa_file)
async def process_sa_file(message: Message, bot: Bot, state: FSMContext):
    sa_path = settings.GOOGLE_SERVICE_ACCOUNT_FILE
    sa_dir = os.path.dirname(sa_path)
    if sa_dir and not os.path.exists(sa_dir):
        os.makedirs(sa_dir, exist_ok=True)

    if message.document:
        file = await bot.get_file(message.document.file_id)
        await bot.download_file(file.file_path, destination=sa_path)
    elif message.text:
        with open(sa_path, "w", encoding="utf-8") as f:
            f.write(message.text)
    else:
        await message.answer("⚠️ Пожалуйста, прикрепите .json файл или отправьте его текст.")
        return

    calendar_service._init_service()
    await state.clear()

    text, keyboard = render_settings_calendar()
    if calendar_service.is_configured():
        confirm_msg = "✅ Файл `service_account.json` успешно сохранен! Google Календарь подключен.\n\n" + text
    else:
        confirm_msg = "⚠️ Файл сохранен, но инициализация Календаря не удалась. Проверьте содержимое JSON.\n\n" + text

    await message.answer(confirm_msg, reply_markup=keyboard, parse_mode="Markdown")


@router.message(SettingsForm.waiting_for_value)
async def process_new_setting_value(message: Message, state: FSMContext):
    data = await state.get_data()
    key_name = data.get("target_key")
    category = data.get("target_cat", "main")
    new_value = message.text.strip() if message.text else ""

    if not key_name:
        await state.clear()
        return

    if key_name == "DEFAULT_SNOOZE_MINUTES":
        if not new_value.isdigit() or int(new_value) <= 0:
            await message.answer("⚠️ Пожалуйста, введите положительное число (количество минут). Пример: `5` или `10`.")
            return
        parsed_val = int(new_value)
        update_env_file(key_name, str(parsed_val))
        setattr(settings, key_name, parsed_val)
    elif key_name == "SCHEDULE_SUMMARY_INTERVAL_HOURS":
        if not new_value.isdigit() or int(new_value) < 0:
            await message.answer("⚠️ Пожалуйста, введите число (интервал в часах, например `3` или `0` для отключения).")
            return
        parsed_val = int(new_value)
        update_env_file(key_name, str(parsed_val))
        setattr(settings, key_name, parsed_val)
        scheduler_service.setup_periodic_schedule_summary(message.from_user.id, parsed_val)
    else:
        update_env_file(key_name, new_value)
        setattr(settings, key_name, new_value)

    # Re-initialize services if needed
    if key_name in ["WEBDAV_LOGIN", "WEBDAV_PASSWORD", "WEBDAV_HOSTNAME", "WEBDAV_VAULT_PATH"]:
        obsidian_service.__init__()
    elif key_name in ["GROQ_API_KEY", "GEMINI_API_KEY"]:
        llm_service.__init__()
    elif key_name == "GOOGLE_CALENDAR_ID":
        calendar_service._init_service()

    await state.clear()

    if category == "llm":
        text, keyboard = render_settings_llm()
    elif category == "obsidian":
        text, keyboard = render_settings_obsidian()
    elif category == "calendar":
        text, keyboard = render_settings_calendar()
    elif category == "snooze":
        text, keyboard = render_settings_snooze()
    else:
        text, keyboard = render_settings_main()

    success_msg = f"✅ Переменная **`{key_name}`** успешно обновлена!\n\n" + text
    await message.answer(success_msg, reply_markup=keyboard, parse_mode="Markdown")
