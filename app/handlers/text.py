import logging
from datetime import date, datetime, timedelta
from typing import Tuple, Optional, Any
from aiogram import Router, Bot, F
from aiogram.enums import ChatAction
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from app.models.schemas import ParsedAction
from app.services.llm import llm_service
from app.services.obsidian import obsidian_service
from app.services.calendar import calendar_service
from app.services.scheduler import scheduler_service
from app.services.notes import notes_service
from app.services.tasks import tasks_service
from app.services.context import context_service
from app.handlers.notes import render_notes_view
from app.utils.timezone import get_today, get_now, get_tz

logger = logging.getLogger(__name__)
router = Router(name="text")


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

    days_acc = ["понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье"]
    if start_date == end_date:
        day_str = days_acc[start_date.weekday()]
        header = f"🌴 **План на {day_str}, {start_date.strftime('%d.%m')}:**\n"
    else:
        header = f"🌴 **Планы с {start_date.strftime('%d.%m')} по {end_date.strftime('%d.%m')}:**\n"

    lines = []
    if timed_items:
        for t_time, icon, text in timed_items:
            lines.append(f"{icon} **{t_time}** • {text}")

    if untimed_items:
        if lines:
            lines.append("")
        lines.append("📌 **Без точного времени:**")
        for icon, text in untimed_items:
            lines.append(f"{icon} {text}")

    d_str = start_date.strftime("%Y-%m-%d")
    buttons = [
        [InlineKeyboardButton(text="📋 Задачи на день", callback_data=f"mng_tasks:{d_str}")],
        [InlineKeyboardButton(text="⏰ Задачи по времени", callback_data=f"mng_rems:{d_str}")]
    ]
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    if not lines:
        if start_date == end_date:
            day_str = days_acc[start_date.weekday()]
            return f"🌴 **На {day_str}, {start_date.strftime('%d.%m')} планов пока нет.**\nВсё свободно!", keyboard
        else:
            return f"🌴 **С {start_date.strftime('%d.%m')} по {end_date.strftime('%d.%m')} планов пока нет.**\nВсё свободно!", keyboard

    return header + "\n" + "\n".join(lines), keyboard


async def render_task_management_view(chat_id: int, target_date: date) -> Tuple[str, InlineKeyboardMarkup]:
    date_formatted = target_date.strftime("%d.%m.%Y")
    d_str = target_date.strftime("%Y-%m-%d")
    local_tasks = await tasks_service.get_tasks(chat_id, target_date)

    if not local_tasks:
        text = f"📋 **Управление задачами на {date_formatted}:**\n\nЗадач на этот день пока нет."
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад к расписанию", callback_data=f"mng_back:{d_str}")]
        ])
        return text, keyboard

    lines = [f"📋 **Управление задачами на {date_formatted}:**\n"]
    buttons = []

    for idx, t in enumerate(local_tasks, 1):
        task_id = t["id"]
        status_icon = "✅" if t.get("is_completed") else "▫️"
        task_text = t["task_text"]
        lines.append(f"{idx}. {status_icon} **{task_text}**")

        toggle_label = "↩️ Отменить" if t.get("is_completed") else "✅ Выполнить"
        buttons.append([
            InlineKeyboardButton(text=f"{toggle_label} #{idx}", callback_data=f"toggle_t:{task_id}:{d_str}"),
            InlineKeyboardButton(text=f"🗑 Удалить #{idx}", callback_data=f"del_t:{task_id}:{d_str}"),
            InlineKeyboardButton(text=f"⏩ На завтра #{idx}", callback_data=f"move_t_next:{task_id}:{d_str}")
        ])

    buttons.append([
        InlineKeyboardButton(text="🔙 Назад к расписанию", callback_data=f"mng_back:{d_str}")
    ])

    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons)


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


async def execute_action_pipeline(bot: Bot, chat_id: int, action: ParsedAction) -> Tuple[str, Optional[InlineKeyboardMarkup]]:
    """
    Executes tasks, calendar events, and scheduled reminders according to ParsedAction.
    Returns (status_text, optional_inline_keyboard).
    """
    # 0. Handle Note Queries ("покажи заметки", "какие у меня заметки")
    if action.is_note_query:
        text, keyboard = await render_notes_view(chat_id)
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

    # 0. Save Task(s) if requested for a date
    if action.is_task_add:
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

        for task_str, t_date in tasks_to_add:
            try:
                await tasks_service.add_task(user_id=chat_id, task_text=task_str, target_date=t_date)
                d_str = t_date.strftime("%d.%m.%Y")
                status_notes.append(f"📋 Задача «{task_str}» сохранена на {d_str}!")
            except Exception as e:
                logger.error(f"Error adding task: {e}")
                status_notes.append(f"⚠️ Ошибка добавления задачи: {e}")

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

    response_text = action.confirmation_text
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


@router.callback_query(F.data.startswith("mng_tasks:"))
async def process_mng_tasks(callback: CallbackQuery):
    date_str = callback.data.split(":", 1)[1]
    target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    text, reply_markup = await render_task_management_view(callback.from_user.id, target_date)
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
    _, task_id_str, date_str = callback.data.split(":")
    task_id = int(task_id_str)
    target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    await tasks_service.toggle_task(callback.from_user.id, task_id)
    text, reply_markup = await render_task_management_view(callback.from_user.id, target_date)
    await safe_edit_markdown(callback.message, text, reply_markup=reply_markup)
    await callback.answer("Статус задачи изменён ✨")


@router.callback_query(F.data.startswith("del_t:"))
async def process_delete_task(callback: CallbackQuery):
    _, task_id_str, date_str = callback.data.split(":")
    task_id = int(task_id_str)
    target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    await tasks_service.delete_task(callback.from_user.id, task_id)
    text, reply_markup = await render_task_management_view(callback.from_user.id, target_date)
    await safe_edit_markdown(callback.message, text, reply_markup=reply_markup)
    await callback.answer("Задача удалена 🗑")


@router.callback_query(F.data.startswith("move_t_next:"))
async def process_move_task_next(callback: CallbackQuery):
    _, task_id_str, date_str = callback.data.split(":")
    task_id = int(task_id_str)
    target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    next_date = target_date + timedelta(days=1)
    await tasks_service.move_task_by_id(callback.from_user.id, task_id, next_date)
    text, reply_markup = await render_task_management_view(callback.from_user.id, target_date)
    await safe_edit_markdown(callback.message, text, reply_markup=reply_markup)
    await callback.answer(f"Задача перенесена на {next_date.strftime('%d.%m.%Y')} 🌴")


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


@router.message(F.text & ~F.text.startswith("/"))
async def handle_text_message(message: Message, bot: Bot):
    user_info = f"user_id={message.from_user.id}"
    if message.from_user.username:
        user_info += f" (@{message.from_user.username})"
    logger.info(f"Received text message from {user_info}: '{message.text}'")
    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    try:
        ctx_date = context_service.get_last_date(message.chat.id)
        parsed_action = await llm_service.parse_user_request(text_content=message.text, context_date=ctx_date)
        reply_text, reply_markup = await execute_action_pipeline(bot, message.chat.id, parsed_action)
        await safe_answer_markdown(message, reply_text, reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Error handling text message: {e}", exc_info=True)
        await message.answer(f"❌ Произошла ошибка при обработке запроса: {str(e)}", parse_mode=None)
