import logging
import re
from datetime import datetime, timedelta
from typing import Optional, List, Set, Tuple
from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from app.services.recurring import recurring_service
from app.services.scheduler import scheduler_service
from app.utils.timezone import get_now

logger = logging.getLogger(__name__)
router = Router(name="habits")

PAGE_SIZE = 5


class HabitAddForm(StatesGroup):
    title = State()
    repeat_type = State()
    days_or_interval = State()
    time = State()


class HabitEditTitleForm(StatesGroup):
    waiting_for_title = State()


class HabitEditTimeForm(StatesGroup):
    waiting_for_time = State()


DAY_CODES_MAP = {
    "mon": "Пн", "tue": "Вт", "wed": "Ср", "thu": "Чт",
    "fri": "Пт", "sat": "Сб", "sun": "Вс"
}

ALL_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def parse_days_from_text(text: str) -> Optional[str]:
    clean = text.lower()
    day_map = {
        "понедельник": "mon", "пн": "mon",
        "вторник": "tue", "вт": "tue",
        "среда": "wed", "среду": "wed", "ср": "wed",
        "четверг": "thu", "чт": "thu",
        "пятница": "fri", "пятницу": "fri", "пт": "fri",
        "суббота": "sat", "субботу": "sat", "сб": "sat",
        "воскресенье": "sun", "вс": "sun"
    }
    found = []
    # Check words
    for word in re.findall(r'[a-яa-z]+', clean):
        if word in day_map and day_map[word] not in found:
            found.append(day_map[word])
    if found:
        return ",".join(found)
    return None


def format_copyable_text(text: str) -> str:
    clean = str(text or "").strip()
    if "\n" in clean or "`" in clean:
        clean_escaped = clean.replace("```", "` ` `")
        return f"```\n{clean_escaped}\n```"
    return f"`{clean}`"


def format_schedule_description(task: dict) -> str:
    r_type = task.get("repeat_type")
    r_time = task.get("target_time") or "10:00"
    cron = (task.get("cron_expression") or "").strip()
    interval = task.get("interval_days")

    if r_type == "daily":
        return f"ежедневно в {r_time}"
    elif r_type == "weekly":
        raw_days = cron.split(",") if cron else []
        formatted_days = ", ".join([DAY_CODES_MAP.get(d.strip(), d.strip()) for d in raw_days if d.strip()]) or "дни не указаны"
        return f"по дням ({formatted_days}) в {r_time}"
    elif r_type == "interval_days" and interval:
        return f"каждые {interval} дн. в {r_time}"
    elif r_type == "interval_hours":
        h_val = interval if (interval and interval > 0) else 1
        return "каждый час" if h_val == 1 else f"каждые {h_val} ч."
    elif r_type == "interval_minutes":
        m_val = interval if (interval and interval > 0) else 30
        return f"каждые {m_val} мин."
    elif r_type == "custom_cron":
        if cron == "0 * * * *" or not cron:
            return "каждый час"
        return f"cron `{cron}`"
    return f"в {r_time}"


async def safe_send_markdown(message: Message, text: str, reply_markup=None):
    try:
        await message.answer(text, reply_markup=reply_markup, parse_mode="Markdown")
    except Exception as e:
        logger.warning(f"Failed to send habits msg with Markdown: {e}")
        await message.answer(text, reply_markup=reply_markup, parse_mode=None)


async def safe_edit_markdown(message: Message, text: str, reply_markup=None):
    try:
        await message.edit_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    except Exception as e:
        logger.warning(f"Failed to edit habits msg with Markdown: {e}")
        await message.edit_text(text, reply_markup=reply_markup, parse_mode=None)


# --- 1. MAIN HABITS LIST VIEW ---

async def render_habits_view(user_id: int, page: int = 1) -> Tuple[str, InlineKeyboardMarkup]:
    tasks = await recurring_service.get_user_tasks(user_id)

    lines = ["🔁 **Повторяющиеся задачи и привычки:**\n"]
    if not tasks:
        lines.append("У тебя пока нет созданных привычек! Нажми «➕ Добавить привычку» ниже ✨\n")
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить привычку", callback_data="add_rec_prompt")]
        ])
        return "\n".join(lines), keyboard

    active_count = sum(1 for t in tasks if t["is_active"])
    lines.append(f"📊 _Активно: {active_count}/{len(tasks)}_\n")

    total_tasks = len(tasks)
    total_pages = max(1, (total_tasks + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(1, min(page, total_pages))

    start_idx = (page - 1) * PAGE_SIZE
    end_idx = min(start_idx + PAGE_SIZE, total_tasks)
    page_tasks = tasks[start_idx:end_idx]

    for idx, t in enumerate(tasks, 1):
        title = t["title"]
        is_act = bool(t["is_active"])
        sched_desc = format_schedule_description(t)
        status_icon = "🟢" if is_act else "⏸"
        lines.append(f"{idx}. {status_icon} **{title}** — _{sched_desc}_")

    lines.append("\nНажмите на номер привычки для управления:")

    buttons = []
    # Row of habit number buttons
    row = []
    for i, t in enumerate(page_tasks, start=start_idx + 1):
        task_id = t["id"]
        row.append(InlineKeyboardButton(text=f" {i} ", callback_data=f"select_h:{task_id}:{page}:{i}"))
        if len(row) == 5:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    # Vertical action buttons per design requirement
    buttons.append([InlineKeyboardButton(text="➕ Добавить привычку", callback_data="add_rec_prompt")])

    # Pagination buttons
    if total_pages > 1:
        pag_row = []
        if page > 1:
            pag_row.append(InlineKeyboardButton(text="⬅️ Пред.", callback_data=f"h_page:{page - 1}"))
        pag_row.append(InlineKeyboardButton(text=f"Стр. {page}/{total_pages}", callback_data="noop"))
        if page < total_pages:
            pag_row.append(InlineKeyboardButton(text="След. ➡️", callback_data=f"h_page:{page + 1}"))
        buttons.append(pag_row)

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    return "\n".join(lines), keyboard


# --- 2. HABIT DETAIL VIEW ---

async def render_habit_detail_view(user_id: int, task_id: int, page: int = 1, idx: int = 1) -> Tuple[str, InlineKeyboardMarkup]:
    task = await recurring_service.get_task_by_id(task_id, user_id)
    if not task:
        text = f"⚠️ Привычка #{idx} не найдена или была удалена."
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад к привычкам", callback_data=f"h_page:{page}")]
        ])
        return text, keyboard

    is_act = bool(task["is_active"])
    status_str = "🟢 **Активна**" if is_act else "⏸ **На паузе**"
    toggle_btn_text = "⏸ Поставить на паузу" if is_act else "▶️ Активировать"
    sched_desc = format_schedule_description(task)
    last_trig = task.get("last_triggered_at") or "ещё не срабатывала"

    text = (
        f"📌 **Управление привычкой #{idx}:**\n\n"
        f"🔁 **{task['title']}**\n\n"
        f"📊 Статус: {status_str}\n"
        f"🗓 Расписание: _{sched_desc}_\n"
        f"⏱ Последний раз: _{last_trig}_"
    )

    buttons = [
        [
            InlineKeyboardButton(text="✏️ Изменить название", callback_data=f"edit_ht_prompt:{task_id}:{page}:{idx}"),
            InlineKeyboardButton(text="⏰ Изменить время", callback_data=f"edit_htm_prompt:{task_id}:{page}:{idx}")
        ],
        [
            InlineKeyboardButton(text=toggle_btn_text, callback_data=f"toggle_rec_det:{task_id}:{page}:{idx}")
        ],
        [
            InlineKeyboardButton(text="🗑 Удалить привычку", callback_data=f"del_rec_det:{task_id}:{page}")
        ],
        [
            InlineKeyboardButton(text="🔙 Назад к привычкам", callback_data=f"h_page:{page}")
        ]
    ]

    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


# --- HANDLERS & ROUTING ---

@router.message(Command("habits"))
@router.message(Command("recurring"))
@router.message(F.text.in_({"🔁 Привычки", "Привычки"}))
async def cmd_habits(message: Message, state: FSMContext):
    await state.clear()
    text, reply_markup = await render_habits_view(message.from_user.id)
    await safe_send_markdown(message, text, reply_markup=reply_markup)


@router.callback_query(F.data == "show_habits")
async def process_show_habits(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    text, reply_markup = await render_habits_view(callback.from_user.id)
    await safe_edit_markdown(callback.message, text, reply_markup=reply_markup)
    await callback.answer()


@router.callback_query(F.data.startswith("h_page:"))
async def process_h_page(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    page = int(callback.data.split(":")[1])
    text, reply_markup = await render_habits_view(callback.from_user.id, page=page)
    await safe_edit_markdown(callback.message, text, reply_markup=reply_markup)
    await callback.answer()


@router.callback_query(F.data.startswith("select_h:"))
async def process_select_habit(callback: CallbackQuery):
    parts = callback.data.split(":")
    task_id = int(parts[1])
    page = int(parts[2]) if len(parts) > 2 else 1
    idx = int(parts[3]) if len(parts) > 3 else 1

    text, reply_markup = await render_habit_detail_view(callback.from_user.id, task_id, page, idx)
    await safe_edit_markdown(callback.message, text, reply_markup=reply_markup)
    await callback.answer()


@router.callback_query(F.data.startswith("toggle_rec_det:"))
async def process_toggle_rec_detail(callback: CallbackQuery):
    parts = callback.data.split(":")
    task_id = int(parts[1])
    page = int(parts[2]) if len(parts) > 2 else 1
    idx = int(parts[3]) if len(parts) > 3 else 1

    res = await recurring_service.toggle_active(task_id, callback.from_user.id)
    if res is not None:
        task = await recurring_service.get_task_by_id(task_id, callback.from_user.id)
        if task:
            if res:
                scheduler_service.schedule_recurring_task_job(task)
                await callback.answer("Привычка активирована 🟢")
            else:
                scheduler_service.unschedule_recurring_task_job(task_id, callback.from_user.id)
                await callback.answer("Привычка поставлена на паузу ⏸")

    text, reply_markup = await render_habit_detail_view(callback.from_user.id, task_id, page, idx)
    await safe_edit_markdown(callback.message, text, reply_markup=reply_markup)


@router.callback_query(F.data.startswith("del_rec_det:"))
@router.callback_query(F.data.startswith("del_rec:"))
async def process_delete_recurring(callback: CallbackQuery):
    parts = callback.data.split(":")
    task_id = int(parts[1])
    page = int(parts[2]) if len(parts) > 2 else 1

    scheduler_service.unschedule_recurring_task_job(task_id, callback.from_user.id)
    await recurring_service.delete_task(task_id, callback.from_user.id)
    await callback.answer("Привычка удалена 🗑")

    text, reply_markup = await render_habits_view(callback.from_user.id, page=page)
    await safe_edit_markdown(callback.message, text, reply_markup=reply_markup)


# Handlers for notification response buttons (✅ Сделано, ⏳ Напомнить 15м, ❌ Пропустить)
@router.callback_query(F.data.startswith("rec_done:"))
async def process_rec_done(callback: CallbackQuery):
    t_id = int(callback.data.split(":", 1)[1])
    task = await recurring_service.get_task_by_id(t_id)
    title = task["title"] if task else "Привычка"
    await callback.answer("Отлично! Выполнено ✅")
    await safe_edit_markdown(callback.message, f"✅ **Привычка выполнена:** «{title}» 🎉")


@router.callback_query(F.data.startswith("rec_snooze:"))
async def process_rec_snooze(callback: CallbackQuery):
    t_id = int(callback.data.split(":", 1)[1])
    task = await recurring_service.get_task_by_id(t_id)
    title = task["title"] if task else "Привычка"

    now = get_now()
    snooze_dt = now + timedelta(minutes=15)
    scheduler_service.schedule_reminder(callback.from_user.id, snooze_dt, f"Напоминание о привычке: {title}")

    await callback.answer("Напоминание отложено на 15 минут ⏳")
    await safe_edit_markdown(callback.message, f"⏳ Напоминание по привычке **«{title}»** отложено на 15 минут.")


# --- 3. HABIT CREATION WIZARD WITH CUSTOM DAYS SELECTOR ---

@router.callback_query(F.data == "add_rec_prompt")
async def process_add_rec_prompt(callback: CallbackQuery, state: FSMContext):
    await state.set_state(HabitAddForm.title)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="show_habits")]
    ])
    text = (
        "✍️ **Создание новой привычки / повторяющейся задачи:**\n\n"
        "Напишите название привычки (например: `Пить воду`, `Зарядка`, `Поливать цветы`).\n\n"
        "_(нажмите кнопку ниже или отправьте /cancel для отмены)_"
    )
    await safe_edit_markdown(callback.message, text, reply_markup=keyboard)
    await callback.answer()


@router.message(Command("cancel"), HabitAddForm.title)
@router.message(Command("cancel"), HabitAddForm.repeat_type)
@router.message(Command("cancel"), HabitAddForm.days_or_interval)
@router.message(Command("cancel"), HabitAddForm.time)
@router.message(F.text.in_({"cancel", "/cancel", "отмена", "Отмена", "❌ Отмена", "быть отменено", "🔙 Отмена"}), HabitAddForm.title)
@router.message(F.text.in_({"cancel", "/cancel", "отмена", "Отмена", "❌ Отмена", "быть отменено", "🔙 Отмена"}), HabitAddForm.repeat_type)
@router.message(F.text.in_({"cancel", "/cancel", "отмена", "Отмена", "❌ Отмена", "быть отменено", "🔙 Отмена"}), HabitAddForm.days_or_interval)
@router.message(F.text.in_({"cancel", "/cancel", "отмена", "Отмена", "❌ Отмена", "быть отменено", "🔙 Отмена"}), HabitAddForm.time)
async def process_cancel_add_rec(message: Message, state: FSMContext):
    await state.clear()
    text, reply_markup = await render_habits_view(message.from_user.id)
    await safe_send_markdown(message, "❌ Создание привычки отменено.", reply_markup=reply_markup)


@router.message(HabitAddForm.title)
async def process_rec_title(message: Message, state: FSMContext):
    title = message.text.strip() if message.text else ""
    if title.lower() in ["/cancel", "cancel", "отмена", "❌ отмена", "быть отменено", "🔙 отмена"]:
        await state.clear()
        text, reply_markup = await render_habits_view(message.from_user.id)
        await safe_send_markdown(message, "❌ Создание привычки отменено.", reply_markup=reply_markup)
        return

    if not title:
        await message.answer("Пожалуйста, введите название привычки.")
        return

    await state.update_data(title=title)
    await state.set_state(HabitAddForm.repeat_type)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 Каждый день (daily)", callback_data="rec_type:daily")],
        [InlineKeyboardButton(text="📆 По дням недели (weekly)", callback_data="rec_type:weekly")],
        [InlineKeyboardButton(text="⏱ С интервалом в N дней", callback_data="rec_type:interval_days")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="show_habits")]
    ])
    await safe_send_markdown(message, f"📌 Привычка: **«{title}»**\n\nВыберите тип повторения:", reply_markup=keyboard)


def render_weekly_days_keyboard(selected_days: Set[str]) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="💼 По будням (Пн-Пт)", callback_data="rec_preset_days:mon,tue,wed,thu,fri")],
        [InlineKeyboardButton(text="🌴 По выходным (Сб-Вс)", callback_data="rec_preset_days:sat,sun")]
    ]

    # Row 1: Mon - Thu
    r1 = []
    for d in ["mon", "tue", "wed", "thu"]:
        icon = "✅" if d in selected_days else "▫️"
        label = DAY_CODES_MAP[d]
        r1.append(InlineKeyboardButton(text=f"{icon} {label}", callback_data=f"toggle_rec_day:{d}"))
    buttons.append(r1)

    # Row 2: Fri - Sun
    r2 = []
    for d in ["fri", "sat", "sun"]:
        icon = "✅" if d in selected_days else "▫️"
        label = DAY_CODES_MAP[d]
        r2.append(InlineKeyboardButton(text=f"{icon} {label}", callback_data=f"toggle_rec_day:{d}"))
    buttons.append(r2)

    # Action row
    sel_labels = ", ".join([DAY_CODES_MAP[d] for d in ALL_DAYS if d in selected_days])
    if selected_days:
        confirm_text = f"➡️ Подтвердить выбор ({sel_labels})"
        buttons.append([InlineKeyboardButton(text=confirm_text, callback_data="confirm_rec_days")])

    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="show_habits")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(F.data.startswith("rec_type:"), HabitAddForm.repeat_type)
async def process_rec_type(callback: CallbackQuery, state: FSMContext):
    r_type = callback.data.split(":", 1)[1]
    await state.update_data(repeat_type=r_type, selected_days=[])

    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="show_habits")]
    ])

    if r_type == "daily":
        await state.set_state(HabitAddForm.time)
        await safe_edit_markdown(
            callback.message,
            "⏰ **Введите время суток (HH:MM):**\nНапример: `09:00`, `11:30`, `18:00`\n\n_(нажмите кнопку ниже или отправьте /cancel для отмены)_",
            reply_markup=cancel_kb
        )
    elif r_type == "weekly":
        await state.set_state(HabitAddForm.days_or_interval)
        keyboard = render_weekly_days_keyboard(set())
        text = (
            "📆 **Выберите дни недели:**\n\n"
            "Вы можете нажать на готовые пресеты выше, переключить отдельные дни кнопками `✅/▫️` "
            "или просто прислать название дней текстом (например: `пн, ср, вс`)."
        )
        await safe_edit_markdown(callback.message, text, reply_markup=keyboard)
    elif r_type == "interval_days":
        await state.set_state(HabitAddForm.days_or_interval)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="Каждые 2 дня", callback_data="rec_interval:2"),
                InlineKeyboardButton(text="Каждые 3 дня", callback_data="rec_interval:3"),
                InlineKeyboardButton(text="Каждые 4 дня", callback_data="rec_interval:4")
            ],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="show_habits")]
        ])
        await safe_edit_markdown(callback.message, "⏱ **Выберите интервал в днях:**", reply_markup=keyboard)

    await callback.answer()


@router.callback_query(F.data.startswith("toggle_rec_day:"), HabitAddForm.days_or_interval)
async def process_toggle_rec_day(callback: CallbackQuery, state: FSMContext):
    day_code = callback.data.split(":")[1]
    data = await state.get_data()
    selected = set(data.get("selected_days", []))

    if day_code in selected:
        selected.remove(day_code)
    else:
        selected.add(day_code)

    await state.update_data(selected_days=list(selected))
    keyboard = render_weekly_days_keyboard(selected)

    sel_labels = ", ".join([DAY_CODES_MAP[d] for d in ALL_DAYS if d in selected]) or "ничего не выбрано"
    text = (
        f"📆 **Выберите дни недели:**\n\n"
        f"Текущий выбор: **{sel_labels}**\n\n"
        f"Нажмите на нужные дни или нажмите «➡️ Подтвердить выбор»."
    )
    await safe_edit_markdown(callback.message, text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("rec_preset_days:"), HabitAddForm.days_or_interval)
async def process_rec_preset_days(callback: CallbackQuery, state: FSMContext):
    days_str = callback.data.split(":", 1)[1]
    await state.update_data(cron_expression=days_str)
    await state.set_state(HabitAddForm.time)

    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="show_habits")]
    ])
    await safe_edit_markdown(
        callback.message,
        "⏰ **Введите время суток (HH:MM):**\nНапример: `09:00`, `11:30`, `18:00`\n\n_(нажмите кнопку ниже или отправьте /cancel для отмены)_",
        reply_markup=cancel_kb
    )
    await callback.answer()


@router.callback_query(F.data == "confirm_rec_days", HabitAddForm.days_or_interval)
async def process_confirm_rec_days(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    selected = set(data.get("selected_days", []))
    if not selected:
        await callback.answer("⚠️ Выберите хотя бы один день недели!", show_alert=True)
        return

    # Sort selected days in mon..sun order
    sorted_days = [d for d in ALL_DAYS if d in selected]
    days_str = ",".join(sorted_days)

    await state.update_data(cron_expression=days_str)
    await state.set_state(HabitAddForm.time)

    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="show_habits")]
    ])
    await safe_edit_markdown(
        callback.message,
        "⏰ **Введите время суток (HH:MM):**\nНапример: `09:00`, `11:30`, `18:00`\n\n_(нажмите кнопку ниже или отправьте /cancel для отмены)_",
        reply_markup=cancel_kb
    )
    await callback.answer()


@router.callback_query(F.data.startswith("rec_interval:"), HabitAddForm.days_or_interval)
async def process_rec_interval(callback: CallbackQuery, state: FSMContext):
    interval_val = int(callback.data.split(":", 1)[1])
    await state.update_data(interval_days=interval_val)
    await state.set_state(HabitAddForm.time)

    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="show_habits")]
    ])
    await safe_edit_markdown(
        callback.message,
        "⏰ **Введите время суток (HH:MM):**\nНапример: `09:00`, `11:30`, `18:00`\n\n_(нажмите кнопку ниже или отправьте /cancel для отмены)_",
        reply_markup=cancel_kb
    )
    await callback.answer()


@router.message(HabitAddForm.days_or_interval)
async def process_rec_days_text_input(message: Message, state: FSMContext):
    text = message.text.strip() if message.text else ""
    if text.lower() in ["/cancel", "cancel", "отмена", "❌ отмена", "быть отменено", "🔙 отмена"]:
        await state.clear()
        text_h, reply_markup = await render_habits_view(message.from_user.id)
        await safe_send_markdown(message, "❌ Создание привычки отменено.", reply_markup=reply_markup)
        return

    days_str = parse_days_from_text(text)
    if not days_str:
        await message.answer("⚠️ Не удалось распознать дни недели. Попробуйте написать, например: `пн, ср, вс` или выберите кнопки выше.")
        return

    await state.update_data(cron_expression=days_str)
    await state.set_state(HabitAddForm.time)

    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="show_habits")]
    ])
    await safe_send_markdown(
        message,
        "⏰ **Введите время суток (HH:MM):**\nНапример: `09:00`, `11:30`, `18:00`\n\n_(нажмите кнопку ниже или отправьте /cancel для отмены)_",
        reply_markup=cancel_kb
    )


@router.message(HabitAddForm.time)
async def process_rec_time(message: Message, state: FSMContext):
    time_str = message.text.strip() if message.text else ""
    if time_str.lower() in ["/cancel", "cancel", "отмена", "❌ отмена", "быть отменено", "🔙 отмена"]:
        await state.clear()
        text, reply_markup = await render_habits_view(message.from_user.id)
        await safe_send_markdown(message, "❌ Создание привычки отменено.", reply_markup=reply_markup)
        return

    try:
        parts = time_str.split(":")
        h, m = int(parts[0]), int(parts[1])
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError
        formatted_time = f"{h:02d}:{m:02d}"
    except Exception:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="show_habits")]
        ])
        await safe_send_markdown(message, "⚠️ Неверный формат времени. Введите время в формате HH:MM (например, `09:00` или `18:30`) или нажмите Отмена.", reply_markup=keyboard)
        return

    data = await state.get_data()
    title = data.get("title")
    r_type = data.get("repeat_type") or "daily"
    cron_expr = data.get("cron_expression")
    interval_days = data.get("interval_days")

    await state.clear()

    task_id = await recurring_service.add_recurring_task(
        user_id=message.from_user.id,
        title=title,
        repeat_type=r_type,
        cron_expression=cron_expr,
        interval_days=interval_days,
        target_time=formatted_time
    )

    if task_id > 0:
        task = await recurring_service.get_task_by_id(task_id, message.from_user.id)
        if task:
            scheduler_service.schedule_recurring_task_job(task)
        await message.answer(f"🔁 **Привычка создана!**\n_«{title}»_", parse_mode="Markdown")

    text, reply_markup = await render_habits_view(message.from_user.id)
    await safe_send_markdown(message, text, reply_markup=reply_markup)


# --- 4. EDIT HABIT HANDLERS ---

@router.callback_query(F.data.startswith("edit_ht_prompt:"))
async def process_edit_habit_title_prompt(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split(":")
    task_id = int(parts[1])
    page = int(parts[2]) if len(parts) > 2 else 1
    idx = int(parts[3]) if len(parts) > 3 else 1

    task = await recurring_service.get_task_by_id(task_id, callback.from_user.id)
    if not task:
        await callback.answer("⚠️ Привычка не найдена.", show_alert=True)
        return

    await state.set_state(HabitEditTitleForm.waiting_for_title)
    await state.update_data(task_id=task_id, page=page, idx=idx)

    copyable_title = format_copyable_text(task['title'])
    text = (
        f"✏️ **Редактирование названия привычки #{idx}:**\n\n"
        f"Текущее название _(нажмите, чтобы скопировать)_:\n{copyable_title}\n\n"
        f"Отправьте новое название привычки сообщением в чат.\n\n"
        f"_(нажмите кнопку ниже или отправьте /cancel для отмены)_"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"select_h:{task_id}:{page}:{idx}")]
    ])
    await safe_edit_markdown(callback.message, text, reply_markup=keyboard)
    await callback.answer()


@router.message(Command("cancel"), HabitEditTitleForm.waiting_for_title)
@router.message(F.text.in_({"cancel", "/cancel", "отмена", "Отмена", "❌ Отмена", "быть отменено", "🔙 Отмена"}), HabitEditTitleForm.waiting_for_title)
async def process_cancel_edit_habit_title(message: Message, state: FSMContext):
    data = await state.get_data()
    task_id = data.get("task_id")
    page = data.get("page", 1)
    idx = data.get("idx", 1)
    await state.clear()

    if task_id:
        text, reply_markup = await render_habit_detail_view(message.from_user.id, task_id, page, idx)
        await safe_send_markdown(message, "❌ Редактирование названия отменено.", reply_markup=reply_markup)
    else:
        text, reply_markup = await render_habits_view(message.from_user.id, page=page)
        await safe_send_markdown(message, text, reply_markup=reply_markup)


@router.message(HabitEditTitleForm.waiting_for_title)
async def process_save_edited_habit_title(message: Message, state: FSMContext):
    data = await state.get_data()
    task_id = data.get("task_id")
    page = data.get("page", 1)
    idx = data.get("idx", 1)
    new_title = message.text.strip() if message.text else ""

    if new_title.lower() in ["/cancel", "cancel", "отмена", "❌ отмена", "быть отменено", "🔙 отмена"]:
        await state.clear()
        text, reply_markup = await render_habit_detail_view(message.from_user.id, task_id, page, idx)
        await safe_send_markdown(message, "❌ Редактирование отменено.", reply_markup=reply_markup)
        return

    await state.clear()
    if task_id and new_title:
        await recurring_service.update_task_title(task_id, message.from_user.id, new_title)
        task = await recurring_service.get_task_by_id(task_id, message.from_user.id)
        if task and task["is_active"]:
            scheduler_service.schedule_recurring_task_job(task)
        await message.answer(f"✨ **Название привычки #{idx} обновлено!**")

    text, reply_markup = await render_habit_detail_view(message.from_user.id, task_id, page, idx)
    await safe_send_markdown(message, text, reply_markup=reply_markup)


@router.callback_query(F.data.startswith("edit_htm_prompt:"))
async def process_edit_habit_time_prompt(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split(":")
    task_id = int(parts[1])
    page = int(parts[2]) if len(parts) > 2 else 1
    idx = int(parts[3]) if len(parts) > 3 else 1

    task = await recurring_service.get_task_by_id(task_id, callback.from_user.id)
    if not task:
        await callback.answer("⚠️ Привычка не найдена.", show_alert=True)
        return

    await state.set_state(HabitEditTimeForm.waiting_for_time)
    await state.update_data(task_id=task_id, page=page, idx=idx)

    copyable_time = format_copyable_text(task.get("target_time") or "10:00")
    text = (
        f"⏰ **Редактирование времени привычки #{idx}:**\n\n"
        f"Текущее время _(нажмите, чтобы скопировать)_:\n{copyable_time}\n\n"
        f"Отправьте новое время в формате HH:MM (например: `09:30`, `18:00`).\n\n"
        f"_(нажмите кнопку ниже или отправьте /cancel для отмены)_"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"select_h:{task_id}:{page}:{idx}")]
    ])
    await safe_edit_markdown(callback.message, text, reply_markup=keyboard)
    await callback.answer()


@router.message(Command("cancel"), HabitEditTimeForm.waiting_for_time)
@router.message(F.text.in_({"cancel", "/cancel", "отмена", "Отмена", "❌ Отмена", "быть отменено", "🔙 Отмена"}), HabitEditTimeForm.waiting_for_time)
async def process_cancel_edit_habit_time(message: Message, state: FSMContext):
    data = await state.get_data()
    task_id = data.get("task_id")
    page = data.get("page", 1)
    idx = data.get("idx", 1)
    await state.clear()

    if task_id:
        text, reply_markup = await render_habit_detail_view(message.from_user.id, task_id, page, idx)
        await safe_send_markdown(message, "❌ Редактирование времени отменено.", reply_markup=reply_markup)
    else:
        text, reply_markup = await render_habits_view(message.from_user.id, page=page)
        await safe_send_markdown(message, text, reply_markup=reply_markup)


@router.message(HabitEditTimeForm.waiting_for_time)
async def process_save_edited_habit_time(message: Message, state: FSMContext):
    data = await state.get_data()
    task_id = data.get("task_id")
    page = data.get("page", 1)
    idx = data.get("idx", 1)
    time_str = message.text.strip() if message.text else ""

    if time_str.lower() in ["/cancel", "cancel", "отмена", "❌ отмена", "быть отменено", "🔙 отмена"]:
        await state.clear()
        text, reply_markup = await render_habit_detail_view(message.from_user.id, task_id, page, idx)
        await safe_send_markdown(message, "❌ Редактирование времени отменено.", reply_markup=reply_markup)
        return

    try:
        parts = time_str.split(":")
        h, m = int(parts[0]), int(parts[1])
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError
        formatted_time = f"{h:02d}:{m:02d}"
    except Exception:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data=f"select_h:{task_id}:{page}:{idx}")]
        ])
        await safe_send_markdown(message, "⚠️ Неверный формат времени. Введите HH:MM (например `09:00`) или нажмите Отмена.", reply_markup=keyboard)
        return

    await state.clear()
    if task_id:
        await recurring_service.update_task_time(task_id, message.from_user.id, formatted_time)
        task = await recurring_service.get_task_by_id(task_id, message.from_user.id)
        if task and task["is_active"]:
            scheduler_service.schedule_recurring_task_job(task)
        await message.answer(f"⏰ **Время привычки #{idx} обновлено на {formatted_time}!**")

    text, reply_markup = await render_habit_detail_view(message.from_user.id, task_id, page, idx)
    await safe_send_markdown(message, text, reply_markup=reply_markup)
