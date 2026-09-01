import logging
from datetime import datetime, timedelta
from typing import Optional, List
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


class HabitAddForm(StatesGroup):
    title = State()
    repeat_type = State()
    days_or_interval = State()
    time = State()


DAY_CODES_MAP = {
    "mon": "Пн", "tue": "Вт", "wed": "Ср", "thu": "Чт",
    "fri": "Пт", "sat": "Сб", "sun": "Вс"
}


def format_schedule_description(task: dict) -> str:
    r_type = task.get("repeat_type")
    r_time = task.get("target_time") or "10:00"
    cron = task.get("cron_expression") or ""
    interval = task.get("interval_days")

    if r_type == "daily":
        return f"ежедневно в {r_time}"
    elif r_type == "weekly":
        raw_days = cron.split(",") if cron else []
        formatted_days = ", ".join([DAY_CODES_MAP.get(d.strip(), d.strip()) for d in raw_days if d.strip()]) or "дни не указаны"
        return f"по дням ({formatted_days}) в {r_time}"
    elif r_type == "interval_days" and interval:
        return f"каждые {interval} дн. в {r_time}"
    elif r_type == "custom_cron":
        return f"cron `{cron}`"
    return f"в {r_time}"


async def render_habits_view(user_id: int):
    tasks = await recurring_service.get_user_tasks(user_id)

    lines = ["🔁 **Повторяющиеся задачи и привычки:**\n"]
    if not tasks:
        lines.append("У тебя пока нет созданных привычек! Нажми «➕ Добавить привычку» или просто напиши мне:\n*«Напоминай каждый день в 11:00 пить воду»* ✨\n")
    else:
        active_count = sum(1 for t in tasks if t["is_active"])
        lines.append(f"📊 _Активно: {active_count}/{len(tasks)}_\n")

    buttons = []

    for idx, t in enumerate(tasks, 1):
        t_id = t["id"]
        title = t["title"]
        is_act = bool(t["is_active"])
        sched_desc = format_schedule_description(t)

        status_icon = "🟢" if is_act else "⏸"
        toggle_label = "⏸ Пауза" if is_act else "▶️ Включить"

        lines.append(f"{idx}. {status_icon} **{title}** — _{sched_desc}_")

        buttons.append([
            InlineKeyboardButton(text=f"{toggle_label} #{idx}", callback_data=f"toggle_rec:{t_id}"),
            InlineKeyboardButton(text=f"🗑 Удалить", callback_data=f"del_rec:{t_id}")
        ])

    buttons.append([
        InlineKeyboardButton(text="➕ Добавить привычку", callback_data="add_rec_prompt")
    ])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    return "\n".join(lines), keyboard


@router.message(Command("habits"))
@router.message(Command("recurring"))
@router.message(F.text.in_({"🔁 Привычки", "Привычки"}))
async def cmd_habits(message: Message, state: FSMContext):
    await state.clear()
    text, reply_markup = await render_habits_view(message.from_user.id)
    await message.answer(text, reply_markup=reply_markup, parse_mode="Markdown")


@router.callback_query(F.data.startswith("toggle_rec:"))
async def process_toggle_recurring(callback: CallbackQuery):
    t_id = int(callback.data.split(":", 1)[1])
    res = await recurring_service.toggle_active(t_id, callback.from_user.id)
    if res is not None:
        task = await recurring_service.get_task_by_id(t_id, callback.from_user.id)
        if task:
            if res:
                scheduler_service.schedule_recurring_task_job(task)
                await callback.answer("Привычка активирована 🟢")
            else:
                scheduler_service.unschedule_recurring_task_job(t_id, callback.from_user.id)
                await callback.answer("Привычка поставлена на паузу ⏸")
    else:
        await callback.answer("Привычка не найдена.")

    text, reply_markup = await render_habits_view(callback.from_user.id)
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    except Exception:
        await callback.message.answer(text, reply_markup=reply_markup, parse_mode="Markdown")


@router.callback_query(F.data.startswith("del_rec:"))
async def process_delete_recurring(callback: CallbackQuery):
    t_id = int(callback.data.split(":", 1)[1])
    scheduler_service.unschedule_recurring_task_job(t_id, callback.from_user.id)
    await recurring_service.delete_task(t_id, callback.from_user.id)
    await callback.answer("Привычка удалена.")

    text, reply_markup = await render_habits_view(callback.from_user.id)
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    except Exception:
        await callback.message.answer(text, reply_markup=reply_markup, parse_mode="Markdown")


# Handlers for notification response buttons (✅ Сделано, ⏳ Напомнить 15м, ❌ Пропустить)
@router.callback_query(F.data.startswith("rec_done:"))
async def process_rec_done(callback: CallbackQuery):
    t_id = int(callback.data.split(":", 1)[1])
    task = await recurring_service.get_task_by_id(t_id)
    title = task["title"] if task else "Привычка"
    await callback.answer("Отлично! Выполнено ✅")
    try:
        await callback.message.edit_text(f"✅ **Привычка выполнена:** «{title}» 🎉", parse_mode="Markdown")
    except Exception:
        await callback.message.answer(f"✅ **Привычка выполнена:** «{title}» 🎉", parse_mode="Markdown")


@router.callback_query(F.data.startswith("rec_snooze:"))
async def process_rec_snooze(callback: CallbackQuery):
    t_id = int(callback.data.split(":", 1)[1])
    task = await recurring_service.get_task_by_id(t_id)
    title = task["title"] if task else "Привычка"

    now = get_now()
    snooze_dt = now + timedelta(minutes=15)
    scheduler_service.schedule_reminder(callback.from_user.id, snooze_dt, f"Напоминание о привычке: {title}")

    await callback.answer("Напоминание отложено на 15 минут ⏳")
    try:
        await callback.message.edit_text(f"⏳ Напоминание по привычке **«{title}»** отложено на 15 минут.", parse_mode="Markdown")
    except Exception:
        await callback.message.answer(f"⏳ Напоминание по привычке **«{title}»** отложено на 15 минут.", parse_mode="Markdown")


@router.callback_query(F.data.startswith("rec_skip:"))
async def process_rec_skip(callback: CallbackQuery):
    t_id = int(callback.data.split(":", 1)[1])
    task = await recurring_service.get_task_by_id(t_id)
    title = task["title"] if task else "Привычка"
    await callback.answer("Напоминание пропущено.")
    try:
        await callback.message.edit_text(f"❌ Напоминание по привычке **«{title}»** пропущено.", parse_mode="Markdown")
    except Exception:
        await callback.message.answer(f"❌ Напоминание по привычке **«{title}»** пропущено.", parse_mode="Markdown")


# FSM Wizard for adding habit manually
@router.callback_query(F.data == "add_rec_prompt")
async def process_add_rec_prompt(callback: CallbackQuery, state: FSMContext):
    await state.set_state(HabitAddForm.title)
    await callback.message.answer(
        "✍️ **Создание новой привычки / повторяющейся задачи:**\n\n"
        "Напишите название привычки (например: `Пить воду`, `Зарядка`, `Поливать цветы`).\n"
        "_(Или наберите `/cancel` для отмены)_"
    )
    await callback.answer()


@router.message(Command("cancel"), HabitAddForm.title)
@router.message(Command("cancel"), HabitAddForm.repeat_type)
@router.message(Command("cancel"), HabitAddForm.days_or_interval)
@router.message(Command("cancel"), HabitAddForm.time)
async def process_cancel_add_rec(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Создание привычки отменено.")


@router.message(HabitAddForm.title)
async def process_rec_title(message: Message, state: FSMContext):
    title = message.text.strip()
    if not title:
        await message.answer("Пожалуйста, введите название привычки.")
        return

    await state.update_data(title=title)
    await state.set_state(HabitAddForm.repeat_type)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 Каждый день (daily)", callback_data="rec_type:daily")],
        [InlineKeyboardButton(text="📆 По дням недели (weekly)", callback_data="rec_type:weekly")],
        [InlineKeyboardButton(text="⏱ С интервалом в N дней", callback_data="rec_type:interval_days")]
    ])
    await message.answer(f"📌 Привычка: **«{title}»**\n\nВыберите тип повторения:", reply_markup=keyboard, parse_mode="Markdown")


@router.callback_query(F.data.startswith("rec_type:"), HabitAddForm.repeat_type)
async def process_rec_type(callback: CallbackQuery, state: FSMContext):
    r_type = callback.data.split(":", 1)[1]
    await state.update_data(repeat_type=r_type)

    if r_type == "daily":
        await state.set_state(HabitAddForm.time)
        await callback.message.edit_text(
            "⏰ **Введите время суток (HH:MM):**\nНапример: `09:00`, `11:30`, `18:00`",
            parse_mode="Markdown"
        )
    elif r_type == "weekly":
        await state.set_state(HabitAddForm.days_or_interval)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="По будням (Пн-Пт)", callback_data="rec_days:mon,tue,wed,thu,fri")],
            [InlineKeyboardButton(text="По выходным (Сб-Вс)", callback_data="rec_days:sat,sun")],
            [InlineKeyboardButton(text="Пн, Ср, Пт", callback_data="rec_days:mon,wed,fri")],
            [InlineKeyboardButton(text="Вт, Чт", callback_data="rec_days:tue,thu")]
        ])
        await callback.message.edit_text("📆 **Выберите дни недели:**", reply_markup=keyboard, parse_mode="Markdown")
    elif r_type == "interval_days":
        await state.set_state(HabitAddForm.days_or_interval)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="Каждые 2 дня", callback_data="rec_interval:2"),
                InlineKeyboardButton(text="Каждые 3 дня", callback_data="rec_interval:3"),
                InlineKeyboardButton(text="Каждые 4 дня", callback_data="rec_interval:4")
            ]
        ])
        await callback.message.edit_text("⏱ **Выберите интервал в днях:**", reply_markup=keyboard, parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data.startswith("rec_days:"), HabitAddForm.days_or_interval)
@router.callback_query(F.data.startswith("rec_interval:"), HabitAddForm.days_or_interval)
async def process_rec_days_or_interval(callback: CallbackQuery, state: FSMContext):
    data_str = callback.data
    if data_str.startswith("rec_days:"):
        days_str = data_str.split(":", 1)[1]
        await state.update_data(cron_expression=days_str)
    elif data_str.startswith("rec_interval:"):
        interval_val = int(data_str.split(":", 1)[1])
        await state.update_data(interval_days=interval_val)

    await state.set_state(HabitAddForm.time)
    await callback.message.edit_text(
        "⏰ **Введите время суток (HH:MM):**\nНапример: `09:00`, `11:30`, `18:00`",
        parse_mode="Markdown"
    )
    await callback.answer()


@router.message(HabitAddForm.time)
async def process_rec_time(message: Message, state: FSMContext):
    time_str = message.text.strip()
    try:
        # Validate HH:MM format
        parts = time_str.split(":")
        h, m = int(parts[0]), int(parts[1])
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError
        formatted_time = f"{h:02d}:{m:02d}"
    except Exception:
        await message.answer("⚠️ Неверный формат времени. Введите время в формате HH:MM (например, `09:00` или `18:30`).")
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
    await message.answer(text, reply_markup=reply_markup, parse_mode="Markdown")
