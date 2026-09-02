import logging
import re
from datetime import date, datetime, timedelta
from typing import Tuple, Optional, Any, List
from aiogram import Router, Bot, F
from aiogram.enums import ChatAction
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import Command

from app.config import settings
from app.models.schemas import ParsedAction
from app.services.llm import llm_service
from app.services.obsidian import obsidian_service
from app.services.calendar import calendar_service
from app.services.scheduler import scheduler_service, get_reminder_inline_keyboard
from app.services.notes import notes_service
from app.services.tasks import tasks_service
from app.services.goals import goals_service
from app.services.recurring import recurring_service
from app.services.context import context_service
from app.handlers.notes import render_notes_view
from app.handlers.goals import render_goals_view
from app.handlers.habits import render_habits_view
from app.utils.timezone import get_today, get_now, get_tz

logger = logging.getLogger(__name__)
router = Router(name="text")


class SnoozeForm(StatesGroup):
    waiting_for_relative_time = State()
    waiting_for_absolute_time = State()


class TaskEditForm(StatesGroup):
    waiting_for_new_text = State()


class TaskMoveForm(StatesGroup):
    waiting_for_date = State()


class TaskTimePromptForm(StatesGroup):
    waiting_for_time = State()


def format_reminder_display_text(r_date_str: str, r_time_str: str, message: str) -> str:
    """
    Formats a reminder string for display. If the reminder message specifies relative offsets
    like 'за час' or 'за пол часа' or 'за N минут', calculates and appends the target event time.
    """
    import re
    from datetime import datetime, timedelta

    msg = message.strip()
    if "точное время" in msg or "(в " in msg:
        return f"{r_date_str} в {r_time_str} — {msg}"

    try:
        r_dt = datetime.strptime(r_time_str, "%H:%M")
        msg_lower = msg.lower()
        offset_minutes = None

        if "за час" in msg_lower or "за 1 час" in msg_lower or "через 1 час" in msg_lower:
            offset_minutes = 60
        elif "за 2 час" in msg_lower or "за 2 часа" in msg_lower:
            offset_minutes = 120
        elif "за пол часа" in msg_lower or "за полчаса" in msg_lower or "за 30 мин" in msg_lower or "за 30 минут" in msg_lower:
            offset_minutes = 30
        elif "за 15 мин" in msg_lower or "за 15 минут" in msg_lower:
            offset_minutes = 15
        else:
            match = re.search(r'за\s+(\d+)\s*мин', msg_lower)
            if match:
                offset_minutes = int(match.group(1))

        if offset_minutes is not None:
            event_dt = r_dt + timedelta(minutes=offset_minutes)
            event_time_str = event_dt.strftime("%H:%M")
            clean_msg = re.sub(r'^(напоминание:\s*|напоминание\s*)', '', msg, flags=re.IGNORECASE).strip()
            return f"{r_date_str} в {r_time_str} — {clean_msg} (точное время {event_time_str})"
    except Exception:
        pass

    return f"{r_date_str} в {r_time_str} — {msg}"


async def render_schedule_view(chat_id: int, start_date: date, end_date: Optional[date] = None) -> Tuple[str, Optional[InlineKeyboardMarkup]]:
    import re
    if not end_date:
        end_date = start_date

    context_service.set_last_date(chat_id, start_date)

    timed_items = []    # list of tuples: (time_str, icon, formatted_text)
    untimed_items = []  # list of tuples: (icon, formatted_text)

    # 1) Get local tasks for date range
    local_tasks = await tasks_service.get_tasks_for_date_range(chat_id, start_date, end_date)
    for t in local_tasks:
        status_icon = "✅" if t.get("is_completed") else "▫️"
        text = t['task_text'].strip()

        match = re.search(r'\b([0-1]?\d|2[0-3]):([0-5]\d)\b', text)
        if match:
            t_time = f"{int(match.group(1)):02d}:{match.group(2)}"
            clean_text = re.sub(r'\s*в\s*\b[0-1]?\d:[0-5]\d\b', '', text, flags=re.IGNORECASE).strip()
            clean_text = re.sub(r'^\b[0-1]?\d:[0-5]\d\b\s*[•\-]?\s*', '', clean_text).strip()
            timed_items.append((t_time, status_icon, clean_text))
        else:
            untimed_items.append((status_icon, text))

    # 2) Get APScheduler reminders for date range
    reminders = scheduler_service.get_reminders_for_date_range(start_date, end_date, chat_id=chat_id)
    for r in reminders:
        formatted_item = format_reminder_display_text(r['date'], r['time'], r['message'])
        formatted_item = formatted_item.replace(f"{r['date']} в ", "")
        time_part = r['time']
        msg_body = re.sub(r'^\d{2}:\d{2}\s*—\s*', '', formatted_item).strip()
        timed_items.append((time_part, "▫️", msg_body))

    # 3) Get Google Calendar events for date range
    events = await calendar_service.get_events_for_date_range(start_date, end_date)
    for ev in events:
        summary = ev.get('summary', 'Без названия')
        start_dt = ev.get('start', {}).get('dateTime', '')
        if len(start_dt) >= 16:
            time_part = start_dt[11:16]
            timed_items.append((time_part, "▫️", summary))
        else:
            untimed_items.append(("▫️", summary))

    # 4) Get Obsidian tasks
    obs_tasks = await obsidian_service.get_daily_tasks(start_date)
    for t in obs_tasks:
        clean_obs = t.strip()
        if clean_obs.startswith("- [x]") or clean_obs.startswith("* [x]"):
            icon = "✅"
            clean_obs = clean_obs[5:].strip()
        elif clean_obs.startswith("- [ ]") or clean_obs.startswith("* [ ]"):
            icon = "▫️"
            clean_obs = clean_obs[5:].strip()
        else:
            icon = "▫️"
        untimed_items.append((icon, clean_obs))

    # Sort timed items chronologically by time (HH:MM)
    timed_items.sort(key=lambda x: x[0])

    # Deduplicate items
    dedup_timed = []
    seen_timed = set()
    for t_time, icon, text in timed_items:
        key = (t_time, text.lower())
        if key not in seen_timed:
            seen_timed.add(key)
            dedup_timed.append((t_time, icon, text))

    dedup_untimed = []
    seen_untimed = set()
    for icon, text in untimed_items:
        key = text.lower()
        if key not in seen_untimed and key not in [t[2].lower() for t in dedup_timed]:
            seen_untimed.add(key)
            dedup_untimed.append((icon, text))

    days_acc = ["понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье"]
    if start_date == end_date:
        day_str = days_acc[start_date.weekday()]
        header = f"🌴 **План на {day_str}, {start_date.strftime('%d.%m')}:**\n"
    else:
        header = f"🌴 **Планы с {start_date.strftime('%d.%m')} по {end_date.strftime('%d.%m')}:**\n"

    lines = []
    if dedup_timed:
        lines.append("⏰ **По времени:**")
        for t_time, icon, text in dedup_timed:
            lines.append(f"{icon} **{t_time}** • {text}")

    if dedup_untimed:
        if lines:
            lines.append("")
        lines.append("📌 **Без точного времени:**")
        for icon, text in dedup_untimed:
            lines.append(f"{icon} {text}")

    d_str = start_date.strftime("%Y-%m-%d")
    task_count = len(local_tasks)
    rem_count = len(reminders)

    task_btn_text = f"📋 Все задачи на день ({task_count})" if task_count > 0 else "📋 Задачи на день"
    rem_btn_text = f"⏰ Напоминания по времени ({rem_count})" if rem_count > 0 else "⏰ Задачи по времени"

    buttons = [
        [InlineKeyboardButton(text=task_btn_text, callback_data=f"mng_tasks:{d_str}")],
        [InlineKeyboardButton(text=rem_btn_text, callback_data=f"mng_rems:{d_str}")]
    ]
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    if not lines:
        if start_date == end_date:
            day_str = days_acc[start_date.weekday()]
            return f"🌴 **На {day_str}, {start_date.strftime('%d.%m')} планов пока нет.**\nВсё свободно!", keyboard
        else:
            return f"🌴 **С {start_date.strftime('%d.%m')} по {end_date.strftime('%d.%m')} планов пока нет.**\nВсё свободно!", keyboard

    return header + "\n" + "\n".join(lines), keyboard


PAGE_SIZE = 10


async def render_task_management_view(chat_id: int, target_date: date, page: int = 1) -> Tuple[str, InlineKeyboardMarkup]:
    date_formatted = target_date.strftime("%d.%m.%Y")
    d_str = target_date.strftime("%Y-%m-%d")
    local_tasks = await tasks_service.get_tasks(chat_id, target_date)

    if not local_tasks:
        text = f"📋 **Управление задачами на {date_formatted}:**\n\nЗадач на этот день пока нет."
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад к расписанию", callback_data=f"mng_back:{d_str}")]
        ])
        return text, keyboard

    total_tasks = len(local_tasks)
    total_pages = max(1, (total_tasks + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(1, min(page, total_pages))

    start_idx = (page - 1) * PAGE_SIZE
    end_idx = min(start_idx + PAGE_SIZE, total_tasks)
    page_tasks = local_tasks[start_idx:end_idx]

    lines = [f"📋 **Управление задачами на {date_formatted}:**\n"]

    for idx, t in enumerate(local_tasks, 1):
        status_icon = "✅" if t.get("is_completed") else "▫️"
        lines.append(f"{idx}. {status_icon} **{t['task_text']}**")

    lines.append("\nНажмите на номер задачи для управления:")

    buttons = []
    row = []
    for i, t in enumerate(page_tasks, start=start_idx + 1):
        task_id = t["id"]
        row.append(InlineKeyboardButton(text=f" {i} ", callback_data=f"select_t:{task_id}:{d_str}:{page}:{i}"))
        if len(row) == 5:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    if total_pages > 1:
        pag_row = []
        if page > 1:
            pag_row.append(InlineKeyboardButton(text="⬅️ Пред.", callback_data=f"t_page:{d_str}:{page - 1}"))
        pag_row.append(InlineKeyboardButton(text=f"Стр. {page}/{total_pages}", callback_data="noop"))
        if page < total_pages:
            pag_row.append(InlineKeyboardButton(text="След. ➡️", callback_data=f"t_page:{d_str}:{page + 1}"))
        buttons.append(pag_row)

    buttons.append([
        InlineKeyboardButton(text="🔙 Назад к расписанию", callback_data=f"mng_back:{d_str}")
    ])

    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons)


async def render_task_detail_view(chat_id: int, task_id: int, date_str: str, page: int = 1, idx: int = 1) -> Tuple[str, InlineKeyboardMarkup]:
    task = await tasks_service.get_task_by_id(chat_id, task_id)
    target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    date_formatted = target_date.strftime("%d.%m.%Y")

    if not task:
        text = f"⚠️ Задача #{idx} не найдена или была удалена."
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад к списку задач", callback_data=f"t_list:{date_str}:{page}")]
        ])
        return text, keyboard

    status_icon = "✅" if task.get("is_completed") else "▫️"
    status_str = "Выполнена" if task.get("is_completed") else "В процессе"
    toggle_label = "↩️ Отменить выполнение" if task.get("is_completed") else "✅ Выполнить"

    text = (
        f"📋 **Управление задачей #{idx}:**\n\n"
        f"{status_icon} **{task['task_text']}**\n"
        f"📅 Дата: **{date_formatted}**\n"
        f"📌 Статус: **{status_str}**"
    )

    buttons = [
        [
            InlineKeyboardButton(text=toggle_label, callback_data=f"toggle_t:{task_id}:{date_str}:{page}:{idx}"),
            InlineKeyboardButton(text="✏️ Изменить", callback_data=f"edit_t:{task_id}:{date_str}:{page}:{idx}")
        ],
        [
            InlineKeyboardButton(text="⏩ Перенести", callback_data=f"move_t_menu:{task_id}:{date_str}:{page}:{idx}"),
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del_t:{task_id}:{date_str}:{page}:{idx}")
        ],
        [
            InlineKeyboardButton(text="🔙 Назад к списку задач", callback_data=f"t_list:{date_str}:{page}")
        ]
    ]

    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


async def render_reminder_management_view(chat_id: int, target_date: date) -> Tuple[str, InlineKeyboardMarkup]:
    date_formatted = target_date.strftime("%d.%m.%Y")
    d_str = target_date.strftime("%Y-%m-%d")
    reminders = scheduler_service.get_reminders_for_date(target_date, chat_id=chat_id)

    if not reminders:
        text = f"⏰ **Задачи по времени (напоминания) на {date_formatted}:**\n\nНапоминаний по времени на этот день нет."
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад к расписанию", callback_data=f"mng_back:{d_str}")]
        ])
        return text, keyboard

    lines = [f"⏰ **Задачи по времени (напоминания) на {date_formatted}:**\n"]
    buttons = []

    for idx, r in enumerate(reminders, 1):
        job_id = r["id"]
        formatted_item = format_reminder_display_text(r['date'], r['time'], r['message'])
        # Strip date prefix for management view
        formatted_item = formatted_item.replace(f"{r['date']} в ", "")
        lines.append(f"{idx}. ⏰ {formatted_item}")

        buttons.append([
            InlineKeyboardButton(text=f"✏️ Изменить #{idx}", callback_data=f"edit_rem:{job_id}:{d_str}"),
            InlineKeyboardButton(text=f"🗑 Удалить #{idx}", callback_data=f"del_rem:{job_id}:{d_str}")
        ])

    buttons.append([
        InlineKeyboardButton(text="🔙 Назад к расписанию", callback_data=f"mng_back:{d_str}")
    ])

    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons)


def has_explicit_time_in_text(text: str) -> bool:
    if not text:
        return False
    t = text.lower()
    if re.search(r'\b(?:[01]?\d|2[0-3])[:\.\-][0-5]\d\b', t):
        return True
    if re.search(r'\bв\s+(?:[01]?\d|2[0-3])\s*(?:ч|час|часа|часов|ч\.)?\b', t):
        return True
    if re.search(r'\bчерез\s+\d+\s*(?:мин|минут|минуты|ч|час|часа|часов)\b', t):
        return True
    if any(kw in t for kw in ["утром", "днем", "днём", "вечером", "ночью"]):
        return True
    return False


async def execute_action_pipeline(bot: Bot, chat_id: int, action: ParsedAction, state: Optional[FSMContext] = None, user_text: str = "") -> Tuple[str, Optional[InlineKeyboardMarkup]]:
    """
    Executes tasks, calendar events, and scheduled reminders according to ParsedAction.
    Returns (status_text, optional_inline_keyboard).
    """
    # 0. Handle Note Queries ("покажи заметки", "какие у меня заметки")
    if action.is_note_query:
        text, keyboard = await render_notes_view(chat_id)
        return text, keyboard

    # 0. Handle Monthly Goals Add & Query ("поставь цель...", "цели на месяц")
    if action.is_goal_add and action.goal_text:
        target_month = action.target_month or get_today().strftime("%Y-%m")
        await goals_service.add_goal(chat_id, action.goal_text, target_month)
        text, keyboard = await render_goals_view(chat_id, target_month)
        return text, keyboard

    if action.is_goals_query:
        target_month = action.target_month or get_today().strftime("%Y-%m")
        text, keyboard = await render_goals_view(chat_id, target_month)
        return text, keyboard

    # 0. Handle Recurring Task / Habit Creation & Query
    if action.is_recurring_add and action.recurring_title:
        title = action.recurring_title.strip()
        r_type = action.repeat_type or "daily"
        cron_expr = ",".join(action.repeat_days) if action.repeat_days else None
        interval_days = action.repeat_interval
        target_time = action.repeat_time or "10:00"

        task_id = await recurring_service.add_recurring_task(
            user_id=chat_id,
            title=title,
            repeat_type=r_type,
            cron_expression=cron_expr,
            interval_days=interval_days,
            target_time=target_time
        )
        if task_id > 0:
            task = await recurring_service.get_task_by_id(task_id, chat_id)
            if task:
                scheduler_service.schedule_recurring_task_job(task)

        text, keyboard = await render_habits_view(chat_id)
        return text, keyboard

    if action.is_recurring_query:
        text, keyboard = await render_habits_view(chat_id)
        return text, keyboard

    # 0. Handle Search Queries ("когда парикмахерская?", "когда врач?")
    if action.is_search_query and action.search_query:
        query = action.search_query.strip()
        lines = [f"🔍 **Результаты поиска по запросу «{query}»:**\n"]
        found_any = False

        # 1. Search local SQLite tasks
        found_tasks = await tasks_service.search_tasks(chat_id, query)
        if found_tasks:
            found_any = True
            lines.append("📋 **Задачи:**")
            for t in found_tasks:
                status_icon = "✅" if t.get("is_completed") else "▫️"
                t_date = datetime.strptime(t['target_date'], "%Y-%m-%d").strftime("%d.%m.%Y")
                lines.append(f"  {status_icon} [{t_date}] {t['task_text']}")
            lines.append("")

        # 2. Search APScheduler reminders
        found_rems = scheduler_service.search_reminders(query, chat_id=chat_id)
        if found_rems:
            found_any = True
            lines.append("🔔 **Напоминания:**")
            for r in found_rems:
                formatted_item = format_reminder_display_text(r['date'], r['time'], r['message'])
                lines.append(f"  • {formatted_item}")
            lines.append("")

        # 3. Search Google Calendar events
        found_events = await calendar_service.search_events(query)
        if found_events:
            found_any = True
            lines.append("📅 **В Google Календаре:**")
            for ev in found_events:
                summary = ev.get('summary', 'Без названия')
                start_dt = ev.get('start', {}).get('dateTime', '') or ev.get('start', {}).get('date', '')
                lines.append(f"  • {start_dt[:10]} — {summary}")
            lines.append("")

        if not found_any:
            return f"🌴 **По запросу «{query}» ни задач, ни напоминаний не найдено.**", None

        return "\n".join(lines).strip(), None

    # 0. Handle Task Rescheduling/Moving ("перемести задачу на 28 число" / "перенеси на 12:10")
    if action.is_task_move:
        moved = await tasks_service.move_task(
            user_id=chat_id,
            query=action.move_task_query or "",
            to_date=action.move_to_date,
            from_date=action.move_from_date,
            new_time=action.move_to_time
        )
        if moved:
            old_str = datetime.strptime(moved['old_date'], "%Y-%m-%d").strftime("%d.%m.%Y")
            new_str = datetime.strptime(moved['new_date'], "%Y-%m-%d").strftime("%d.%m.%Y")
            if old_str == new_str and action.move_to_time:
                return f"🌴 **Готово!** Время задачи **«{moved['task_text']}»** изменено на **{action.move_to_time}** ✨", None
            else:
                time_info = f" (время: {action.move_to_time})" if action.move_to_time else ""
                return f"🌴 **Готово!** Перенёс задачу **«{moved['task_text']}»** с {old_str} на {new_str}{time_info} ✨", None
        else:
            # Fallback: check if query matches a scheduled reminder in APScheduler
            found_rems = scheduler_service.search_reminders(action.move_task_query or "", chat_id=chat_id)
            if found_rems:
                rem_job = found_rems[0]
                new_trigger_dt = None
                target_date = action.move_to_date or action.move_from_date or get_today()
                if action.move_to_time:
                    try:
                        h, m = map(int, action.move_to_time.split(":"))
                        new_trigger_dt = datetime.combine(target_date, datetime.min.time().replace(hour=h, minute=m), tzinfo=get_tz())
                    except Exception:
                        pass
                
                updated = scheduler_service.update_reminder(rem_job["id"], new_trigger_at=new_trigger_dt)
                if updated:
                    t_str = action.move_to_time or rem_job['time']
                    return f"🌴 **Готово!** Время напоминания **«{rem_job['message']}»** изменено на **{t_str}** ✨", None

            q_name = action.move_task_query or "указанную"
            return f"⚠️ Задача или напоминание по запросу «{q_name}» не найдена.", None

    # 0. Handle Task Clearing ("убери все задачи", "очисти задачи")
    if action.is_task_clear:
        target_clear_date = action.clear_date or context_service.get_last_date(chat_id)
        count = await tasks_service.clear_tasks_for_date(chat_id, target_clear_date)
        if target_clear_date:
            d_str = target_clear_date.strftime("%d.%m.%Y")
            return f"🌴 Все задачи на {d_str} успешно удалены (всего: {count}) 🗑", None
        else:
            return f"🌴 Все ваши задачи успешно удалены (всего: {count}) 🗑", None

    # 0. Handle Single Task Deletion ("удали задачу...")
    if action.is_task_delete_single and action.delete_task_query:
        target_del_date = action.task_date or context_service.get_last_date(chat_id)
        deleted = await tasks_service.delete_task_by_query(chat_id, action.delete_task_query, target_del_date)
        if deleted:
            return f"🌴 Задача **«{action.delete_task_query}»** успешно удалена 🗑", None
        else:
            return f"⚠️ Задача по запросу «{action.delete_task_query}» не найдена.", None

    # 0. Handle Schedule Queries (e.g. "какие планы на сегодня", "планы на завтра", "что 3 сентября")
    if action.is_schedule_query and action.query_date:
        return await render_schedule_view(chat_id, action.query_date, action.query_end_date)

    status_notes = []
    added_task_ids = []

    # 0. Save Task(s) if requested for a date or reminder without explicit time
    if action.is_task_add or (action.reminders and not action.event_start):
        tasks_to_add = []
        if action.tasks:
            for t in action.tasks:
                if t.task_text and t.task_text.strip():
                    t_date = t.task_date or action.task_date or get_today()
                    tasks_to_add.append((t.task_text.strip(), t_date))
        elif action.task_text:
            t_date = action.task_date or get_today()
            raw_lines = [line.strip("-*• 1234567890.").strip() for line in action.task_text.split("\n") if line.strip()]
            for line in raw_lines:
                if line:
                    tasks_to_add.append((line, t_date))
        elif action.reminders:
            for r in action.reminders:
                if r.message and r.message.strip():
                    t_date = r.trigger_at.date() if r.trigger_at else get_today()
                    tasks_to_add.append((r.message.strip(), t_date))

        for task_str, t_date in tasks_to_add:
            try:
                task_id = await tasks_service.add_task(user_id=chat_id, task_text=task_str, target_date=t_date)
                added_task_ids.append((task_id, task_str, t_date))
                d_str = t_date.strftime("%d.%m.%Y")
                status_notes.append(f"📋 Задача «{task_str}» сохранена на {d_str}!")
            except Exception as e:
                logger.error(f"Error adding task: {e}")
                status_notes.append(f"⚠️ Ошибка добавления задачи: {e}")

    # Check if explicit time was specified in user text / request
    explicit_time = has_explicit_time_in_text(user_text) or action.event_start is not None

    if added_task_ids and not explicit_time and state:
        action.reminders = []

        first_id, first_text, first_date = added_task_ids[0]
        await state.set_state(TaskTimePromptForm.waiting_for_time)
        await state.update_data(
            task_id=first_id,
            task_text=first_text,
            target_date_str=first_date.strftime("%Y-%m-%d")
        )
        d_str = first_date.strftime("%d.%m.%Y")
        prompt_text = (
            f"📋 Задача **«{first_text}»** сохранена на {d_str}!\n\n"
            f"⏰ **На какое время поставить напоминание?**\n\n"
            f"Напишите время (например: `14:30`, `в 18:00`) или нажмите/напишите **«Без времени»** (или «нет»).\n\n"
            f"_(При выборе «Без времени» бот автоматически напомнит вам в 08:00, 12:00, 15:00 и 19:00)_"
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔔 Без времени", callback_data=f"task_time:no_time:{first_id}")],
            [InlineKeyboardButton(text="❌ Без напоминаний", callback_data=f"task_time:skip:{first_id}")]
        ])
        return prompt_text, keyboard

    # 0. Save Quick Note ONLY if explicitly requested
    if action.is_note_save and action.note_content:
        try:
            await notes_service.add_note(user_id=chat_id, content=action.note_content)
            status_notes.append(f"📌 Заметка «{action.note_content}» сохранена!")
        except Exception as e:
            logger.error(f"Error saving note: {e}")
            status_notes.append(f"⚠️ Ошибка сохранения заметки: {e}")

    # 1. Sync task with Obsidian WebDAV (or fallback to local Tasks if WebDAV is not configured)
    if action.obsidian_entry and not action.is_task_add:
        if obsidian_service.is_configured():
            try:
                path = await obsidian_service.add_task_to_daily_note(
                    task_text=action.obsidian_entry.task_text,
                    target_date=action.obsidian_entry.entry_date,
                    target_section=action.obsidian_entry.target_section
                )
                if path:
                    status_notes.append(f"📝 Задача добавлена в Obsidian (`{path}`)")
            except Exception as e:
                logger.error(f"Obsidian sync failed: {e}")
        else:
            try:
                t_date = action.obsidian_entry.entry_date or get_today()
                await tasks_service.add_task(user_id=chat_id, task_text=action.obsidian_entry.task_text, target_date=t_date)
                d_str = t_date.strftime("%d.%m.%Y")
                status_notes.append(f"📋 Задача «{action.obsidian_entry.task_text}» добавлена на {d_str}")
            except Exception as e:
                logger.error(f"Error saving fallback task: {e}")

    # 2. Add Google Calendar event
    if action.event_start:
        try:
            event = await calendar_service.create_event(
                title=action.title,
                start_time=action.event_start,
                end_time=action.event_end,
                description=action.description
            )
            if event:
                status_notes.append("📅 Событие добавлено в Google Календарь")
        except Exception as e:
            logger.error(f"Calendar creation failed: {e}")
            status_notes.append(f"⚠️ Ошибка создания события в календаре: {e}")

    # 3. Schedule Telegram reminders
    if action.reminders:
        now = get_now()
        tz = get_tz()
        for r in action.reminders:
            try:
                trigger_dt = r.trigger_at
                if trigger_dt.tzinfo is None:
                    trigger_dt = trigger_dt.replace(tzinfo=tz)

                if trigger_dt <= now:
                    time_str = trigger_dt.strftime("%d.%m.%Y в %H:%M")
                    logger.info(f"Skipping reminder '{r.message}' for chat {chat_id} because trigger_at ({time_str}) is in the past.")
                    status_notes.append(f"⚠️ Напоминание на {time_str} не запланировано (дата уже прошла)")
                    continue

                scheduler_service.schedule_reminder(
                    chat_id=chat_id,
                    trigger_at=trigger_dt,
                    message=r.message
                )
                time_str = trigger_dt.strftime("%d.%m.%Y в %H:%M")
                status_notes.append(f"⏰ Запланировано напоминание на {time_str}")
            except Exception as e:
                logger.error(f"Scheduling reminder failed: {e}")
                status_notes.append(f"⚠️ Ошибка планирования напоминания: {e}")

    response_text = action.confirmation_text or "🌴 Готово!"
    if status_notes:
        response_text += "\n\n" + "\n".join(status_notes)

    return response_text, None


async def safe_answer_markdown(message: Message, text: str, reply_markup=None):
    try:
        await message.answer(text, reply_markup=reply_markup, parse_mode="Markdown")
    except Exception as e:
        logger.warning(f"Failed to send with Markdown parse_mode: {e}. Falling back to plain text.")
        await message.answer(text, reply_markup=reply_markup, parse_mode=None)


async def safe_edit_markdown(message: Message, text: str, reply_markup=None):
    try:
        await message.edit_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    except Exception as e:
        logger.warning(f"Failed to edit message with Markdown parse_mode: {e}. Falling back to plain text.")
        await message.edit_text(text, reply_markup=reply_markup, parse_mode=None)


@router.callback_query(F.data == "noop")
async def process_noop(callback: CallbackQuery):
    await callback.answer()


@router.callback_query(F.data.startswith("mng_tasks:"))
async def process_mng_tasks(callback: CallbackQuery):
    parts = callback.data.split(":")
    date_str = parts[1]
    page = int(parts[2]) if len(parts) > 2 else 1
    target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    text, reply_markup = await render_task_management_view(callback.from_user.id, target_date, page)
    await safe_edit_markdown(callback.message, text, reply_markup=reply_markup)
    await callback.answer()


@router.callback_query(F.data.startswith("t_page:"))
@router.callback_query(F.data.startswith("t_list:"))
async def process_t_list_page(callback: CallbackQuery):
    parts = callback.data.split(":")
    date_str = parts[1]
    page = int(parts[2]) if len(parts) > 2 else 1
    target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    text, reply_markup = await render_task_management_view(callback.from_user.id, target_date, page)
    await safe_edit_markdown(callback.message, text, reply_markup=reply_markup)
    await callback.answer()


@router.callback_query(F.data.startswith("select_t:"))
async def process_select_task(callback: CallbackQuery):
    parts = callback.data.split(":")
    task_id = int(parts[1])
    date_str = parts[2]
    page = int(parts[3]) if len(parts) > 3 else 1
    idx = int(parts[4]) if len(parts) > 4 else 1

    text, reply_markup = await render_task_detail_view(callback.from_user.id, task_id, date_str, page, idx)
    await safe_edit_markdown(callback.message, text, reply_markup=reply_markup)
    await callback.answer()


@router.callback_query(F.data.startswith("mng_rems:"))
async def process_mng_rems(callback: CallbackQuery):
    date_str = callback.data.split(":", 1)[1]
    target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    text, reply_markup = await render_reminder_management_view(callback.from_user.id, target_date)
    await safe_edit_markdown(callback.message, text, reply_markup=reply_markup)
    await callback.answer()


@router.callback_query(F.data.startswith("mng_back:"))
async def process_mng_back(callback: CallbackQuery):
    date_str = callback.data.split(":", 1)[1]
    target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    text, reply_markup = await render_schedule_view(callback.from_user.id, target_date)
    await safe_edit_markdown(callback.message, text, reply_markup=reply_markup)
    await callback.answer()


@router.callback_query(F.data.startswith("toggle_t:"))
async def process_toggle_task(callback: CallbackQuery):
    parts = callback.data.split(":")
    task_id = int(parts[1])
    date_str = parts[2]
    page = int(parts[3]) if len(parts) > 3 else 1
    idx = int(parts[4]) if len(parts) > 4 else 1

    await tasks_service.toggle_task(callback.from_user.id, task_id)
    text, reply_markup = await render_task_detail_view(callback.from_user.id, task_id, date_str, page, idx)
    await safe_edit_markdown(callback.message, text, reply_markup=reply_markup)
    await callback.answer("Статус задачи изменён ✨")


@router.callback_query(F.data.startswith("del_t:"))
async def process_delete_task(callback: CallbackQuery):
    parts = callback.data.split(":")
    task_id = int(parts[1])
    date_str = parts[2]
    page = int(parts[3]) if len(parts) > 3 else 1

    await tasks_service.delete_task(callback.from_user.id, task_id)
    target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    text, reply_markup = await render_task_management_view(callback.from_user.id, target_date, page)
    await safe_edit_markdown(callback.message, text, reply_markup=reply_markup)
    await callback.answer("Задача удалена 🗑")


# --- TASK MOVE HANDLERS & HELPERS ---

def parse_target_date(user_input: str, base_date: date) -> Optional[date]:
    text = user_input.lower().strip()
    if not text:
        return None

    match_full = re.search(r'\b(\d{1,2})[\.\/](\d{1,2})(?:[\.\/](\d{2,4}))?\b', text)
    if match_full:
        d = int(match_full.group(1))
        m = int(match_full.group(2))
        y = base_date.year
        if match_full.group(3):
            y_val = int(match_full.group(3))
            y = y_val if y_val > 100 else 2000 + y_val
        try:
            return date(y, m, d)
        except ValueError:
            pass

    if "послезавтра" in text:
        return base_date + timedelta(days=2)

    if "завтра" in text:
        return base_date + timedelta(days=1)

    match_days = re.search(r'через\s+(\d+)\s*(?:дня|дней|день|д)?', text)
    if match_days:
        try:
            days = int(match_days.group(1))
            return base_date + timedelta(days=days)
        except ValueError:
            pass

    if "через неделю" in text or ("след" in text and "недель" in text):
        return base_date + timedelta(days=7)

    return None


@router.callback_query(F.data.startswith("move_t_menu:"))
async def process_move_task_menu(callback: CallbackQuery):
    parts = callback.data.split(":")
    task_id = int(parts[1])
    date_str = parts[2]
    page = parts[3] if len(parts) > 3 else "1"
    idx = parts[4] if len(parts) > 4 else "1"

    task = await tasks_service.get_task_by_id(callback.from_user.id, task_id)
    if not task:
        await callback.answer("⚠️ Задача не найдена или уже удалена.", show_alert=True)
        return

    text = (
        f"⏩ **Перенос задачи:**\n\n"
        f"Задача: **\"{task['task_text']}\"**\n\n"
        f"Выберите вариант переноса:"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏩ Перенести на завтра", callback_data=f"move_t_opt:tomorrow:{task_id}:{date_str}:{page}:{idx}")],
        [InlineKeyboardButton(text="📅 Перенести на след. неделю", callback_data=f"move_t_opt:next_week:{task_id}:{date_str}:{page}:{idx}")],
        [InlineKeyboardButton(text="✍️ Указать дату (свободный ввод)", callback_data=f"move_t_opt:custom:{task_id}:{date_str}:{page}:{idx}")],
        [InlineKeyboardButton(text="🔙 Отмена", callback_data=f"select_t:{task_id}:{date_str}:{page}:{idx}")]
    ])
    await safe_edit_markdown(callback.message, text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("move_t_opt:"))
async def process_move_task_option(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split(":")
    opt_type = parts[1]
    task_id = int(parts[2])
    date_str = parts[3]
    page = parts[4] if len(parts) > 4 else "1"
    idx = parts[5] if len(parts) > 5 else "1"
    target_date = datetime.strptime(date_str, "%Y-%m-%d").date()

    task = await tasks_service.get_task_by_id(callback.from_user.id, task_id)
    if not task:
        await callback.answer("⚠️ Задача не найдена или уже удалена.", show_alert=True)
        return

    if opt_type == "tomorrow":
        to_date = target_date + timedelta(days=1)
        await tasks_service.move_task_by_id(callback.from_user.id, task_id, to_date)
        text, reply_markup = await render_task_management_view(callback.from_user.id, target_date, int(page))
        await safe_edit_markdown(callback.message, text, reply_markup=reply_markup)
        await callback.answer(f"Задача перенесена на завтра ({to_date.strftime('%d.%m.%Y')}) 🌴")

    elif opt_type == "next_week":
        to_date = target_date + timedelta(days=7)
        await tasks_service.move_task_by_id(callback.from_user.id, task_id, to_date)
        text, reply_markup = await render_task_management_view(callback.from_user.id, target_date, int(page))
        await safe_edit_markdown(callback.message, text, reply_markup=reply_markup)
        await callback.answer(f"Задача перенесена на след. неделю ({to_date.strftime('%d.%m.%Y')}) 🌴")

    elif opt_type == "custom":
        await state.set_state(TaskMoveForm.waiting_for_date)
        await state.update_data(task_id=task_id, target_date_str=date_str, task_text=task['task_text'], page=page, idx=idx)

        text = (
            f"✍️ **На какую дату перенести задачу?**\n\n"
            f"Задача: **\"{task['task_text']}\"**\n\n"
            f"Напишите дату (например: `05.09`, `15 сентября`, `через 3 дня`, `в пятницу`):\n\n"
            f"_(нажмите кнопку ниже или отправьте /cancel для отмены)_"
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Отмена", callback_data=f"select_t:{task_id}:{date_str}:{page}:{idx}")]
        ])
        await safe_edit_markdown(callback.message, text, reply_markup=keyboard)
        await callback.answer()


@router.message(Command("cancel"), TaskMoveForm.waiting_for_date)
@router.message(F.text.in_({"cancel", "/cancel", "отмена", "Отмена", "❌ Отмена", "🔙 Отмена"}), TaskMoveForm.waiting_for_date)
async def cmd_cancel_task_move(message: Message, state: FSMContext):
    data = await state.get_data()
    date_str = data.get("target_date_str")
    page = data.get("page", "1")
    await state.clear()
    if date_str:
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        text, reply_markup = await render_task_management_view(message.from_user.id, target_date, int(page))
        await safe_answer_markdown(message, "❌ Перенос задачи отменён.", reply_markup=reply_markup)
    else:
        await message.answer("❌ Перенос задачи отменён.")


@router.message(TaskMoveForm.waiting_for_date)
async def process_task_move_date_input(message: Message, state: FSMContext):
    data = await state.get_data()
    task_id = data.get("task_id")
    date_str = data.get("target_date_str")
    page = data.get("page", "1")
    task_text = data.get("task_text", "Задача")
    user_input = message.text.strip() if message.text else ""

    if user_input.lower() in ["/cancel", "cancel", "отмена", "❌ отмена", "🔙 отмена"]:
        await cmd_cancel_task_move(message, state)
        return

    if not task_id or not user_input:
        await message.answer("⚠️ Пожалуйста, укажите дату для переноса (или /cancel).")
        return

    base_date = datetime.strptime(date_str, "%Y-%m-%d").date() if date_str else get_today()
    parsed_date = parse_target_date(user_input, base_date)

    if parsed_date is None:
        try:
            parsed_action = await llm_service.parse_user_request(
                text_content=f"перенеси задачу {task_text} на {user_input}"
            )
            if parsed_action.move_to_date:
                parsed_date = parsed_action.move_to_date
            elif parsed_action.task_date:
                parsed_date = parsed_action.task_date
            elif parsed_action.query_date:
                parsed_date = parsed_action.query_date
        except Exception as e:
            logger.error(f"LLM fallback error for custom task move date: {e}")

    if parsed_date is None:
        await message.answer("⚠️ Не удалось распознать дату. Попробуйте написать, например: `05.09`, `завтра` или `через 3 дня` (или `/cancel`).")
        return

    success = await tasks_service.move_task_by_id(message.from_user.id, task_id, parsed_date)
    await state.clear()

    target_date = datetime.strptime(date_str, "%Y-%m-%d").date() if date_str else get_today()
    text, reply_markup = await render_task_management_view(message.from_user.id, target_date, int(page))

    if success:
        date_formatted = parsed_date.strftime("%d.%m.%Y")
        confirm_msg = f"✅ Задача **\"{task_text}\"** перенесена на **{date_formatted}** 🌴\n\n" + text
        await safe_answer_markdown(message, confirm_msg, reply_markup=reply_markup)
    else:
        await message.answer("⚠️ Не удалось перенести задачу. Возможно, она была удалена.", reply_markup=reply_markup)


# --- TASK TIME PROMPT HANDLERS & HELPERS ---

def schedule_default_4_reminders(chat_id: int, task_text: str, target_date: Optional[date] = None) -> List[str]:
    """
    Schedules 4 default daily reminders (08:00, 12:00, 15:00, 19:00) for a task.
    Returns list of formatted time strings that were scheduled.
    """
    if not target_date:
        target_date = get_today()

    now = get_now()
    tz = get_tz()

    default_times = [(8, 0), (12, 0), (15, 0), (19, 0)]
    scheduled_times = []

    for h, m in default_times:
        dt = datetime.combine(target_date, datetime.min.time()).replace(hour=h, minute=m, tzinfo=tz)
        if dt <= now and target_date == get_today():
            dt = dt + timedelta(days=1)

        msg = f"{task_text}"
        scheduler_service.schedule_reminder(chat_id=chat_id, trigger_at=dt, message=msg)
        scheduled_times.append(dt.strftime("%H:%M"))

    return scheduled_times


@router.callback_query(F.data.startswith("task_time:no_time:"))
async def process_task_time_no_time(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    task_id = int(callback.data.split(":")[2])
    task = await tasks_service.get_task_by_id(callback.from_user.id, task_id)
    if not task:
        await callback.answer("⚠️ Задача не найдена.", show_alert=True)
        return

    t_date = datetime.strptime(task["target_date"], "%Y-%m-%d").date()
    times = schedule_default_4_reminders(callback.message.chat.id, task["task_text"], t_date)

    times_str = ", ".join(times) if times else "08:00, 12:00, 15:00, 19:00"
    msg = (
        f"📋 Задача **«{task['task_text']}»** сохранена!\n\n"
        f"⏰ Автоматически запланированы 4 напоминания: **{times_str}**."
    )
    await safe_edit_markdown(callback.message, msg)
    await callback.answer("Запланировано 4 напоминания ⏰")


@router.callback_query(F.data.startswith("task_time:skip:"))
async def process_task_time_skip(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    task_id = int(callback.data.split(":")[2])
    task = await tasks_service.get_task_by_id(callback.from_user.id, task_id)
    task_text = task["task_text"] if task else "Задача"

    msg = f"📋 Задача **«{task_text}»** сохранена без напоминаний."
    await safe_edit_markdown(callback.message, msg)
    await callback.answer("Сохранено без напоминаний")


@router.message(Command("cancel"), TaskTimePromptForm.waiting_for_time)
@router.message(F.text.in_({"cancel", "/cancel", "отмена", "Отмена", "❌ Отмена", "🔙 Отмена"}), TaskTimePromptForm.waiting_for_time)
async def cmd_cancel_task_time(message: Message, state: FSMContext):
    data = await state.get_data()
    task_text = data.get("task_text", "Задача")
    await state.clear()
    await message.answer(f"📋 Задача **«{task_text}»** сохранена без точного времени.")


@router.message(TaskTimePromptForm.waiting_for_time)
async def process_task_time_input(message: Message, state: FSMContext):
    data = await state.get_data()
    task_id = data.get("task_id")
    task_text = data.get("task_text", "Задача")
    date_str = data.get("target_date_str")
    user_input = message.text.strip() if message.text else ""

    if user_input.lower() in ["/cancel", "cancel", "отмена", "❌ отмена", "быть отменено", "🔙 отмена"]:
        await cmd_cancel_task_time(message, state)
        return

    lower_input = user_input.lower()
    no_time_keywords = ["без времени", "без", "нет", "не надо", "нет не надо", "без напом", "no", "none"]

    if any(kw == lower_input or kw in lower_input for kw in no_time_keywords):
        t_date = datetime.strptime(date_str, "%Y-%m-%d").date() if date_str else get_today()
        times = schedule_default_4_reminders(message.chat.id, task_text, t_date)
        await state.clear()
        times_str = ", ".join(times) if times else "08:00, 12:00, 15:00, 19:00"
        await message.answer(
            f"📋 Задача **«{task_text}»** сохранена!\n\n"
            f"⏰ Автоматически запланированы 4 напоминания: **{times_str}**.",
            parse_mode="Markdown"
        )
        return

    now = get_now()
    target_dt = parse_absolute_datetime(user_input, now)

    if target_dt is None:
        try:
            parsed_action = await llm_service.parse_user_request(
                text_content=f"напомни {task_text} {user_input}"
            )
            if parsed_action.reminders and parsed_action.reminders[0].trigger_at:
                trig = parsed_action.reminders[0].trigger_at
                if trig.tzinfo is None:
                    trig = trig.replace(tzinfo=get_tz())
                if trig <= now:
                    trig += timedelta(days=1)
                target_dt = trig
        except Exception as e:
            logger.error(f"LLM fallback error for task time prompt: {e}")

    if target_dt is None:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔔 Без времени", callback_data=f"task_time:no_time:{task_id}")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data=f"task_time:skip:{task_id}")]
        ])
        await message.answer(
            "⚠️ Не удалось распознать время. Напишите время (например: `14:30`, `в 18:00`) или ответьте **«без времени»** (или /cancel).",
            reply_markup=keyboard
        )
        return

    scheduler_service.schedule_reminder(
        chat_id=message.chat.id,
        trigger_at=target_dt,
        message=task_text
    )
    await state.clear()
    date_format = "%H:%M" if target_dt.date() == now.date() else "%d.%m.%Y в %H:%M"
    time_str = target_dt.strftime(date_format)
    await message.answer(
        f"✅ Задача **«{task_text}»** сохранена!\n\n⏰ Напоминание запланировано на **{time_str}**.",
        parse_mode="Markdown"
    )


@router.callback_query(F.data.startswith("edit_t:"))
async def process_edit_task_callback(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split(":")
    task_id = int(parts[1])
    date_str = parts[2]
    page = parts[3] if len(parts) > 3 else "1"
    idx = parts[4] if len(parts) > 4 else "1"

    task = await tasks_service.get_task_by_id(callback.from_user.id, task_id)
    if not task:
        await callback.answer("⚠️ Задача не найдена или уже удалена.", show_alert=True)
        return

    await state.set_state(TaskEditForm.waiting_for_new_text)
    await state.update_data(task_id=task_id, target_date_str=date_str, page=page, idx=idx)

    text = (
        f"✏️ **Редактирование задачи #{idx}:**\n\n"
        f"Текущий текст: **\"{task['task_text']}\"**\n\n"
        f"Отправьте новый текст для этой задачи сообщением в чат:\n\n"
        f"_(нажмите кнопку ниже или отправьте /cancel для отмены)_"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Отмена", callback_data=f"select_t:{task_id}:{date_str}:{page}:{idx}")]
    ])
    await safe_edit_markdown(callback.message, text, reply_markup=keyboard)
    await callback.answer()


@router.message(Command("cancel"), TaskEditForm.waiting_for_new_text)
@router.message(F.text.in_({"cancel", "/cancel", "отмена", "Отмена", "❌ Отмена", "🔙 Отмена"}), TaskEditForm.waiting_for_new_text)
async def cmd_cancel_task_edit(message: Message, state: FSMContext):
    data = await state.get_data()
    task_id = data.get("task_id")
    date_str = data.get("target_date_str")
    page = data.get("page", "1")
    idx = data.get("idx", "1")
    await state.clear()
    if task_id and date_str:
        text, reply_markup = await render_task_detail_view(message.from_user.id, task_id, date_str, int(page), int(idx))
        await safe_answer_markdown(message, "❌ Редактирование задачи отменено.", reply_markup=reply_markup)
    elif date_str:
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        text, reply_markup = await render_task_management_view(message.from_user.id, target_date, int(page))
        await safe_answer_markdown(message, "❌ Редактирование задачи отменено.", reply_markup=reply_markup)
    else:
        await message.answer("❌ Редактирование задачи отменено.")


@router.message(TaskEditForm.waiting_for_new_text)
async def process_task_edit_input(message: Message, state: FSMContext):
    data = await state.get_data()
    task_id = data.get("task_id")
    date_str = data.get("target_date_str")
    page = data.get("page", "1")
    idx = data.get("idx", "1")
    new_text = message.text.strip() if message.text else ""

    if new_text.lower() in ["/cancel", "cancel", "отмена", "❌ отмена", "🔙 отмена"]:
        await cmd_cancel_task_edit(message, state)
        return

    if not task_id or not new_text:
        await message.answer("⚠️ Текст задачи не может быть пустым. Введите новый текст задачи (или /cancel).")
        return

    success = await tasks_service.update_task_text(message.from_user.id, task_id, new_text)
    await state.clear()

    if date_str and success:
        text, reply_markup = await render_task_detail_view(message.from_user.id, task_id, date_str, int(page), int(idx))
        confirm_msg = f"✅ Текст задачи успешно изменён на: **\"{new_text}\"**!\n\n" + text
        await safe_answer_markdown(message, confirm_msg, reply_markup=reply_markup)
    elif date_str:
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        text, reply_markup = await render_task_management_view(message.from_user.id, target_date, int(page))
        await message.answer("⚠️ Не удалось обновить задачу. Возможно, она была удалена.", reply_markup=reply_markup)
    else:
        await message.answer(f"✅ Текст задачи успешно изменён на: **\"{new_text}\"**!")


@router.callback_query(F.data.startswith("edit_rem:"))
async def process_edit_reminder(callback: CallbackQuery):
    _, job_id, date_str = callback.data.split(":")
    target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    reminders = scheduler_service.get_reminders_for_date(target_date, chat_id=callback.from_user.id)
    rem = next((r for r in reminders if r["id"] == job_id), None)
    
    if not rem:
        await callback.answer("⚠️ Напоминание не найдено или уже удалено.", show_alert=True)
        return

    text = (
        f"✏️ **Редактирование напоминания:**\n\n"
        f"Текущее: ⏰ **{rem['time']} — {rem['message']}** (дата: {rem['date']})\n\n"
        f"Вы можете написать прямо в чат другое время или текст, например:\n"
        f"`перенеси {rem['message']} на 15:00` или `измени время {rem['time']} на 16:30`!"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад к напоминаниям", callback_data=f"mng_rems:{date_str}")]
    ])
    await safe_edit_markdown(callback.message, text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("del_rem:"))
async def process_delete_reminder(callback: CallbackQuery):
    _, job_id, date_str = callback.data.split(":")
    target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    scheduler_service.remove_reminder(job_id)
    text, reply_markup = await render_reminder_management_view(callback.from_user.id, target_date)
    await safe_edit_markdown(callback.message, text, reply_markup=reply_markup)
    await callback.answer("Напоминание удалено 🗑")


# --- SNOOZE HANDLERS & HELPERS ---

def extract_reminder_text(full_text: str) -> str:
    text = full_text or ""
    if "💤" in text:
        text = text.split("💤", 1)[0]
    text = re.sub(r'^.*?Напоминание:?\s*\**\s*', '', text, flags=re.DOTALL | re.IGNORECASE).strip()
    return text


def parse_relative_minutes(user_input: str) -> Optional[int]:
    text = user_input.lower().strip()
    if not text:
        return None

    if "полчаса" in text or "пол часа" in text:
        return 30

    match_h = re.search(r'(\d+(?:[\.,]\d+)?)\s*(?:час|часа|часов|ч|h)', text)
    if match_h:
        try:
            val = float(match_h.group(1).replace(',', '.'))
            return int(val * 60)
        except ValueError:
            pass

    match_m = re.search(r'(\d+)\s*(?:мин|минут|минуты|м|m)?', text)
    if match_m:
        try:
            return int(match_m.group(1))
        except ValueError:
            pass

    return None


def parse_absolute_datetime(user_input: str, current_dt: datetime) -> Optional[datetime]:
    text = user_input.lower().strip()
    if not text:
        return None

    tz = get_tz()
    if current_dt.tzinfo is None:
        current_dt = current_dt.replace(tzinfo=tz)
    else:
        current_dt = current_dt.astimezone(tz)

    is_tomorrow = "завтра" in text

    match_time = re.search(r'(?:в\s*)?([0-1]?\d|2[0-3])[:\.\s]([0-5]\d)', text)
    if match_time:
        h = int(match_time.group(1))
        m = int(match_time.group(2))

        target_date = current_dt.date()
        if is_tomorrow:
            target_date += timedelta(days=1)

        candidate_dt = datetime.combine(target_date, datetime.min.time()).replace(hour=h, minute=m, tzinfo=tz)
        if not is_tomorrow and candidate_dt <= current_dt:
            candidate_dt += timedelta(days=1)
        return candidate_dt

    match_hour_only = re.search(r'\bв\s*([0-1]?\d|2[0-3])\b', text)
    if match_hour_only:
        h = int(match_hour_only.group(1))
        target_date = current_dt.date()
        if is_tomorrow:
            target_date += timedelta(days=1)
        candidate_dt = datetime.combine(target_date, datetime.min.time()).replace(hour=h, minute=0, tzinfo=tz)
        if not is_tomorrow and candidate_dt <= current_dt:
            candidate_dt += timedelta(days=1)
        return candidate_dt

    return None


@router.callback_query(F.data == "snooze:def")
async def process_snooze_def(callback: CallbackQuery):
    msg_text = extract_reminder_text(callback.message.text or callback.message.caption or "")
    minutes = getattr(settings, "DEFAULT_SNOOZE_MINUTES", 5)
    now = get_now()
    new_trigger_at = now + timedelta(minutes=minutes)

    scheduler_service.schedule_reminder(
        chat_id=callback.message.chat.id,
        trigger_at=new_trigger_at,
        message=msg_text
    )

    time_str = new_trigger_at.strftime("%H:%M")
    updated_text = (
        f"⏰ **Напоминание:**\n\n{msg_text}\n\n"
        f"💤 *Отложено на {minutes} мин (до {time_str})*"
    )

    keyboard = get_reminder_inline_keyboard(minutes)

    await safe_edit_markdown(callback.message, updated_text, reply_markup=keyboard)
    await callback.answer(f"⏰ Отложено на {minutes} мин (до {time_str})")


@router.callback_query(F.data == "snooze:rel")
async def process_snooze_rel(callback: CallbackQuery, state: FSMContext):
    msg_text = extract_reminder_text(callback.message.text or callback.message.caption or "")
    await state.set_state(SnoozeForm.waiting_for_relative_time)
    await state.update_data(reminder_text=msg_text, original_msg_id=callback.message.message_id)

    prompt = (
        f"⏱ **На сколько минут отложить?**\n\n"
        f"Напоминание: *\"{msg_text}\"*\n\n"
        f"Напишите время (например: `10`, `15 минут`, `30 мин`, `1.5 часа`):\n\n"
        f"_(нажмите кнопку ниже или отправьте /cancel для отмены)_"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_snooze")]
    ])
    await callback.message.answer(prompt, reply_markup=keyboard, parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data == "snooze:abs")
async def process_snooze_abs(callback: CallbackQuery, state: FSMContext):
    msg_text = extract_reminder_text(callback.message.text or callback.message.caption or "")
    await state.set_state(SnoozeForm.waiting_for_absolute_time)
    await state.update_data(reminder_text=msg_text, original_msg_id=callback.message.message_id)

    prompt = (
        f"🕒 **На какое время отложить?**\n\n"
        f"Напоминание: *\"{msg_text}\"*\n\n"
        f"Укажите время или дату (например: `18:00`, `в 15:30`, `завтра в 10:00`):\n\n"
        f"_(нажмите кнопку ниже или отправьте /cancel для отмены)_"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_snooze")]
    ])
    await callback.message.answer(prompt, reply_markup=keyboard, parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data == "cancel_snooze")
async def process_cancel_snooze_callback(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer("Откладывание отменено.")
    try:
        await callback.message.edit_text("❌ Откладывание напоминания отменено.")
    except Exception:
        await callback.message.answer("❌ Откладывание напоминания отменено.")


@router.message(Command("cancel"), SnoozeForm.waiting_for_relative_time)
@router.message(Command("cancel"), SnoozeForm.waiting_for_absolute_time)
@router.message(F.text.in_({"cancel", "/cancel", "отмена", "Отмена", "❌ Отмена", "быть отменено", "🔙 Отмена"}), SnoozeForm.waiting_for_relative_time)
@router.message(F.text.in_({"cancel", "/cancel", "отмена", "Отмена", "❌ Отмена", "быть отменено", "🔙 Отмена"}), SnoozeForm.waiting_for_absolute_time)
async def cmd_cancel_snooze(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Откладывание напоминания отменено.")


@router.message(SnoozeForm.waiting_for_relative_time)
async def process_snooze_relative_input(message: Message, state: FSMContext):
    data = await state.get_data()
    msg_text = data.get("reminder_text", "Напоминание")
    user_input = message.text.strip() if message.text else ""

    if user_input.lower() in ["/cancel", "cancel", "отмена", "❌ отмена", "быть отменено", "🔙 отмена"]:
        await cmd_cancel_snooze(message, state)
        return

    minutes = parse_relative_minutes(user_input)
    now = get_now()

    if minutes is None:
        try:
            parsed_action = await llm_service.parse_user_request(
                text_content=f"напомни через {user_input} {msg_text}"
            )
            if parsed_action.reminders and parsed_action.reminders[0].trigger_at:
                trig = parsed_action.reminders[0].trigger_at
                if trig.tzinfo is None:
                    trig = trig.replace(tzinfo=get_tz())
                delta = trig - now
                minutes = max(1, int(delta.total_seconds() / 60))
        except Exception as e:
            logger.error(f"LLM fallback error for relative snooze: {e}")

    if minutes is None or minutes <= 0:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_snooze")]
        ])
        await message.answer("⚠️ Не удалось распознать время. Попробуйте написать, например: `15`, `20 минут` или `1.5 часа` (или /cancel).", reply_markup=keyboard)
        return

    new_trigger_at = now + timedelta(minutes=minutes)
    scheduler_service.schedule_reminder(
        chat_id=message.chat.id,
        trigger_at=new_trigger_at,
        message=msg_text
    )
    time_str = new_trigger_at.strftime("%H:%M")
    await state.clear()
    await message.answer(f"💤 Напоминание **\"{msg_text}\"** отложено на **{minutes} мин** (до {time_str}) ⏰", parse_mode="Markdown")


@router.message(SnoozeForm.waiting_for_absolute_time)
async def process_snooze_absolute_input(message: Message, state: FSMContext):
    data = await state.get_data()
    msg_text = data.get("reminder_text", "Напоминание")
    user_input = message.text.strip() if message.text else ""

    if user_input.lower() in ["/cancel", "cancel", "отмена", "❌ отмена", "быть отменено", "🔙 отмена"]:
        await cmd_cancel_snooze(message, state)
        return

    now = get_now()
    target_dt = parse_absolute_datetime(user_input, now)

    if target_dt is None:
        try:
            parsed_action = await llm_service.parse_user_request(
                text_content=f"напомни {msg_text} {user_input}"
            )
            if parsed_action.reminders and parsed_action.reminders[0].trigger_at:
                trig = parsed_action.reminders[0].trigger_at
                if trig.tzinfo is None:
                    trig = trig.replace(tzinfo=get_tz())
                if trig <= now:
                    trig += timedelta(days=1)
                target_dt = trig
        except Exception as e:
            logger.error(f"LLM fallback error for absolute snooze: {e}")

    if target_dt is None:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_snooze")]
        ])
        await message.answer("⚠️ Не удалось распознать время. Попробуйте написать, например: `18:00`, `в 15:30` или `завтра в 10:00` (или /cancel).", reply_markup=keyboard)
        return

    scheduler_service.schedule_reminder(
        chat_id=message.chat.id,
        trigger_at=target_dt,
        message=msg_text
    )

    date_format = "%H:%M" if target_dt.date() == now.date() else "%d.%m.%Y в %H:%M"
    time_str = target_dt.strftime(date_format)
    await state.clear()
    await message.answer(f"💤 Напоминание **\"{msg_text}\"** перенесено на **{time_str}** ⏰", parse_mode="Markdown")


@router.message(Command("today"))
@router.message(F.text.in_({"📅 Планы на сегодня", "Планы на сегодня"}))
async def cmd_today(message: Message):
    today = get_today()
    text, reply_markup = await render_schedule_view(message.chat.id, today, today)
    await message.answer(text, reply_markup=reply_markup, parse_mode="Markdown")


@router.message(F.text & ~F.text.startswith("/"))
async def handle_text_message(message: Message, bot: Bot, state: FSMContext):
    user_info = f"user_id={message.from_user.id}"
    if message.from_user.username:
        user_info += f" (@{message.from_user.username})"
    logger.info(f"Received text message from {user_info}: '{message.text}'")
    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    try:
        ctx_date = context_service.get_last_date(message.chat.id)
        parsed_action = await llm_service.parse_user_request(text_content=message.text, context_date=ctx_date)
        reply_text, reply_markup = await execute_action_pipeline(bot, message.chat.id, parsed_action, state=state, user_text=message.text)
        await safe_answer_markdown(message, reply_text, reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Error handling text message: {e}", exc_info=True)
        await message.answer(f"❌ Произошла ошибка при обработке запроса: {str(e)}", parse_mode=None)
