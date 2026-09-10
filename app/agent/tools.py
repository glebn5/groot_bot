import logging
from datetime import date, datetime, timedelta
from typing import Optional, List, Dict, Any

from app.agent.schemas import ToolExecutionContext, ToolResult
from app.agent.time_parser import parse_date_str, parse_datetime_str, ensure_tz_aware, is_past_datetime
from app.services.tasks import tasks_service
from app.services.scheduler import scheduler_service
from app.services.calendar import calendar_service
from app.services.notes import notes_service
from app.services.goals import goals_service
from app.services.recurring import recurring_service
from app.services.obsidian import obsidian_service
from app.services.context import context_service
from app.utils.timezone import get_now, get_today, get_tz

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. TASKS TOOLS
# ---------------------------------------------------------------------------

async def create_task(context: ToolExecutionContext, text: str, target_date: Optional[str] = None) -> ToolResult:
    """
    Creates a new daily task for the user on target_date (YYYY-MM-DD or relative like 'завтра').
    """
    if not text or not text.strip():
        return ToolResult(success=False, error_code="invalid_argument", message="Task text cannot be empty.")

    clean_text = text.strip()
    import re

    # If target_date wasn't explicitly passed, check if text contains date phrase like "на завтра", "на пятницу"
    t_date = parse_date_str(target_date) if target_date else None
    if not t_date:
        date_match = re.search(r'\bна\s+(сегодня|завтра|послезавтра|понедельник|вторник|среду|среда|четверг|пятницу|пятница|субботу|суббота|воскресенье|\d{1,2}\s+[а-яё]+)\b', clean_text.lower())
        if date_match:
            t_date = parse_date_str(date_match.group(1))

    # Strip operational command prefixes (e.g. "добавь на завтра:", "поставь задачу:", "закинь еще:")
    clean_text = re.sub(
        r'^(?:добавь|поставь|закинь|создай|запиши|напиши)\s+(?:мне\s+)?(?:еще\s+|ещё\s+)?(?:задачу\s+)?(?:на\s+[^\s:]+\s*)?[:\-]?\s*',
        '',
        clean_text,
        flags=re.IGNORECASE
    ).strip()
    clean_text = re.sub(r'^(?:задача|дело|план)[:\-]?\s*', '', clean_text, flags=re.IGNORECASE).strip()
    if not clean_text:
        clean_text = text.strip()

    if not t_date:
        # Fall back to recently discussed date in user context, or today
        ctx_date = context_service.get_last_date(context.user_id)
        t_date = ctx_date or get_today()

    # Idempotency check: check if same task was added today
    existing_tasks = await tasks_service.get_tasks(context.user_id, t_date)
    for ext in existing_tasks:
        if ext["task_text"].lower() == clean_text.lower():
            logger.info(f"Duplicate task detected: #{ext['id']} '{clean_text}'. Returning existing.")
            return ToolResult(
                success=True,
                entity_type="task",
                entity_id=ext["id"],
                data={"id": ext["id"], "task_text": clean_text, "target_date": t_date.strftime("%Y-%m-%d"), "already_existed": True},
                message=f"Задача «{clean_text}» уже существует на {t_date.strftime('%d.%m.%Y')}."
            )

    try:
        task_id = await tasks_service.add_task(user_id=context.user_id, task_text=clean_text, target_date=t_date)
        context_service.set_last_entity(context.user_id, "task", {
            "id": task_id,
            "text": clean_text,
            "date": t_date.strftime("%Y-%m-%d")
        })
        context_service.set_last_date(context.user_id, t_date)

        return ToolResult(
            success=True,
            entity_type="task",
            entity_id=task_id,
            data={"id": task_id, "task_text": clean_text, "target_date": t_date.strftime("%Y-%m-%d")},
            message=f"Задача «{clean_text}» создана на {t_date.strftime('%d.%m.%Y')}."
        )
    except Exception as e:
        logger.error(f"Error in create_task: {e}", exc_info=True)
        return ToolResult(success=False, error_code="db_error", message=str(e))


async def get_tasks(context: ToolExecutionContext, start_date: str, end_date: Optional[str] = None) -> ToolResult:
    """
    Retrieves tasks for user between start_date and end_date.
    """
    s_date = parse_date_str(start_date) or get_today()
    e_date = parse_date_str(end_date) if end_date else s_date
    if not e_date:
        e_date = s_date

    try:
        tasks = await tasks_service.get_tasks_for_date_range(context.user_id, s_date, e_date)
        return ToolResult(
            success=True,
            entity_type="task_list",
            data=tasks,
            message=f"Найдено {len(tasks)} задач за период с {s_date.strftime('%d.%m.%Y')} по {e_date.strftime('%d.%m.%Y')}."
        )
    except Exception as e:
        logger.error(f"Error in get_tasks: {e}", exc_info=True)
        return ToolResult(success=False, error_code="db_error", message=str(e))


async def search_tasks(context: ToolExecutionContext, query: str) -> ToolResult:
    """
    Searches user tasks matching keywords.
    """
    if not query or not query.strip():
        return ToolResult(success=False, error_code="invalid_argument", message="Query cannot be empty.")

    try:
        tasks = await tasks_service.search_tasks(context.user_id, query.strip())
        return ToolResult(
            success=True,
            entity_type="task_list",
            data=tasks,
            message=f"По запросу «{query}» найдено задач: {len(tasks)}."
        )
    except Exception as e:
        logger.error(f"Error in search_tasks: {e}", exc_info=True)
        return ToolResult(success=False, error_code="db_error", message=str(e))


async def complete_task(context: ToolExecutionContext, task_id: Optional[int] = None) -> ToolResult:
    """
    Marks a task as completed by ID (or the last discussed task if task_id is not specified).
    """
    if not task_id:
        last_t = context_service.get_last_entity(context.user_id, "task")
        if last_t and "id" in last_t:
            task_id = last_t["id"]
        else:
            return ToolResult(success=False, error_code="not_found", message="Не удалось определить, какую именно задачу завершить. Уточните название.")

    try:
        task = await tasks_service.get_task_by_id(context.user_id, task_id)
        if not task:
            return ToolResult(success=False, error_code="not_found", message=f"Задача #{task_id} не найдена.")

        ok = await tasks_service.complete_task(context.user_id, task_id)
        if ok:
            return ToolResult(
                success=True,
                entity_type="task",
                entity_id=task_id,
                data={"id": task_id, "is_completed": 1, "task_text": task["task_text"]},
                message=f"Задача «{task['task_text']}» отмечена выполненной."
            )
        return ToolResult(success=False, error_code="db_error", message="Failed to mark task completed.")
    except Exception as e:
        logger.error(f"Error in complete_task: {e}", exc_info=True)
        return ToolResult(success=False, error_code="db_error", message=str(e))


async def move_task(context: ToolExecutionContext, target_date: str, task_id: Optional[int] = None, new_time: Optional[str] = None) -> ToolResult:
    """
    Moves a task to a new target date and optionally sets new time in task text.
    If task_id is not specified, uses the last discussed task from context.
    """
    if not task_id:
        last_t = context_service.get_last_entity(context.user_id, "task")
        if last_t and "id" in last_t:
            task_id = last_t["id"]
        else:
            return ToolResult(success=False, error_code="not_found", message="Не удалось определить, какую именно задачу перенести. Уточните название.")

    t_date = parse_date_str(target_date)
    if not t_date:
        return ToolResult(success=False, error_code="invalid_date", message=f"Не удалось распознать дату «{target_date}».")

    try:
        task = await tasks_service.get_task_by_id(context.user_id, task_id)
        if not task:
            return ToolResult(success=False, error_code="not_found", message=f"Задача #{task_id} не найдена.")

        ok = await tasks_service.move_task_by_id(context.user_id, task_id, t_date)
        if not ok:
            return ToolResult(success=False, error_code="db_error", message="Не удалось обновить дату задачи.")

        updated_text = task["task_text"]
        if new_time and new_time.strip():
            import re
            clean_time = new_time.strip()
            time_match = re.search(r'\b([0-1]?\d|2[0-3]):([0-5]\d)\b', updated_text)
            if time_match:
                updated_text = re.sub(r'\b([0-1]?\d|2[0-3]):([0-5]\d)\b', clean_time, updated_text, count=1)
            else:
                updated_text = f"{clean_time} • {updated_text}"
            await tasks_service.update_task_text(context.user_id, task_id, updated_text)

        context_service.set_last_entity(context.user_id, "task", {
            "id": task_id,
            "text": updated_text,
            "date": t_date.strftime("%Y-%m-%d")
        })
        context_service.set_last_date(context.user_id, t_date)

        return ToolResult(
            success=True,
            entity_type="task",
            entity_id=task_id,
            data={"id": task_id, "task_text": updated_text, "new_date": t_date.strftime("%Y-%m-%d")},
            message=f"Задача перенесена на {t_date.strftime('%d.%m.%Y')}."
        )
    except Exception as e:
        logger.error(f"Error in move_task: {e}", exc_info=True)
        return ToolResult(success=False, error_code="db_error", message=str(e))


async def update_task(
    context: ToolExecutionContext,
    task_id: Optional[int] = None,
    new_text: Optional[str] = None,
    target_date: Optional[str] = None
) -> ToolResult:
    """
    Updates the text and/or target date of an existing task by ID (or the last discussed task).
    """
    if not task_id:
        last_t = context_service.get_last_entity(context.user_id, "task")
        if last_t and "id" in last_t:
            task_id = last_t["id"]
        else:
            return ToolResult(success=False, error_code="not_found", message="Не удалось определить задачу для обновления.")

    try:
        task = await tasks_service.get_task_by_id(context.user_id, task_id)
        if not task:
            return ToolResult(success=False, error_code="not_found", message=f"Задача #{task_id} не найдена.")

        if new_text and new_text.strip():
            await tasks_service.update_task_text(context.user_id, task_id, new_text.strip())

        if target_date:
            t_date = parse_date_str(target_date)
            if t_date:
                await tasks_service.move_task_by_id(context.user_id, task_id, t_date)

        updated = await tasks_service.get_task_by_id(context.user_id, task_id)
        return ToolResult(
            success=True,
            entity_type="task",
            entity_id=task_id,
            data=updated,
            message="Задача обновлена."
        )
    except Exception as e:
        logger.error(f"Error in update_task: {e}", exc_info=True)
        return ToolResult(success=False, error_code="db_error", message=str(e))


async def delete_task(context: ToolExecutionContext, task_id: Optional[int] = None) -> ToolResult:
    """
    Deletes a single task by ID (or the last discussed task).
    """
    if not task_id:
        last_t = context_service.get_last_entity(context.user_id, "task")
        if last_t and "id" in last_t:
            task_id = last_t["id"]
        else:
            return ToolResult(success=False, error_code="not_found", message="Не удалось определить задачу для удаления.")

    try:
        task = await tasks_service.get_task_by_id(context.user_id, task_id)
        if not task:
            return ToolResult(success=False, error_code="not_found", message=f"Задача #{task_id} не найдена.")

        ok = await tasks_service.delete_task(context.user_id, task_id)
        if ok:
            return ToolResult(
                success=True,
                entity_type="task",
                entity_id=task_id,
                message=f"Задача «{task['task_text']}» удалена."
            )
        return ToolResult(success=False, error_code="db_error", message="Failed to delete task.")
    except Exception as e:
        logger.error(f"Error in delete_task: {e}", exc_info=True)
        return ToolResult(success=False, error_code="db_error", message=str(e))


async def clear_tasks(context: ToolExecutionContext, target_date: Optional[str] = None, confirmed: bool = False) -> ToolResult:
    """
    Clears all tasks for a date (or all tasks across all dates if target_date is not specified).
    Requires confirmed=True to prevent accidental data loss.
    """
    t_date = parse_date_str(target_date) if target_date else None

    if not confirmed:
        d_label = f"на {t_date.strftime('%d.%m.%Y')}" if t_date else "ЗА ВСЁ ВРЕМЯ"
        return ToolResult(
            success=False,
            error_code="confirmation_required",
            data={"action": "clear_tasks", "target_date": target_date},
            message=f"⚠️ Требуется подтверждение: вы действительно хотите удалить все задачи {d_label}?"
        )

    try:
        count = await tasks_service.clear_tasks_for_date(context.user_id, t_date)
        d_label = f"на {t_date.strftime('%d.%m.%Y')}" if t_date else "за всё время"
        return ToolResult(
            success=True,
            entity_type="task_clear",
            data={"deleted_count": count, "target_date": target_date},
            message=f"Удалено {count} задач {d_label}."
        )
    except Exception as e:
        logger.error(f"Error in clear_tasks: {e}", exc_info=True)
        return ToolResult(success=False, error_code="db_error", message=str(e))


# ---------------------------------------------------------------------------
# 2. REMINDER TOOLS
# ---------------------------------------------------------------------------

async def create_reminder(context: ToolExecutionContext, message: str, trigger_at: str) -> ToolResult:
    """
    Creates a one-off Telegram reminder scheduled at trigger_at (ISO datetime or expression like '2026-09-11 15:00').
    """
    if not message or not message.strip():
        return ToolResult(success=False, error_code="invalid_argument", message="Reminder message cannot be empty.")

    clean_msg = message.strip()
    target_dt = parse_datetime_str(trigger_at)
    if not target_dt:
        return ToolResult(success=False, error_code="invalid_datetime", message=f"Не удалось распознать время напоминания «{trigger_at}». Укажите точное время.")

    target_dt = ensure_tz_aware(target_dt)
    now = get_now()

    if target_dt <= now:
        return ToolResult(
            success=False,
            error_code="past_datetime",
            message=f"Время {target_dt.strftime('%d.%m.%Y в %H:%M')} уже прошло. Пожалуйста, укажите время в будущем."
        )

    # Idempotency check: check if identical reminder is already scheduled within 2 minutes
    existing_rems = scheduler_service.get_reminders_for_date(target_dt.date(), chat_id=context.chat_id)
    time_hm = target_dt.strftime("%H:%M")
    for r in existing_rems:
        if r["time"] == time_hm and r["message"].lower() == clean_msg.lower():
            logger.info(f"Duplicate reminder detected: #{r['id']} at {time_hm}. Returning existing.")
            return ToolResult(
                success=True,
                entity_type="reminder",
                entity_id=r["id"],
                data={"id": r["id"], "message": clean_msg, "trigger_at": target_dt.isoformat(), "already_existed": True},
                message=f"Напоминание «{clean_msg}» уже запланировано на {target_dt.strftime('%d.%m.%Y в %H:%M')}."
            )

    try:
        job_id = scheduler_service.schedule_reminder(
            chat_id=context.chat_id,
            trigger_at=target_dt,
            message=clean_msg
        )
        context_service.set_last_entity(context.user_id, "reminder", {
            "id": job_id,
            "text": clean_msg,
            "datetime": target_dt.isoformat(),
            "time": target_dt.strftime("%H:%M")
        })
        context_service.set_last_date(context.user_id, target_dt.date())

        return ToolResult(
            success=True,
            entity_type="reminder",
            entity_id=job_id,
            data={"id": job_id, "message": clean_msg, "trigger_at": target_dt.isoformat()},
            message=f"Напоминание запланировано на {target_dt.strftime('%d.%m.%Y в %H:%M')}."
        )
    except Exception as e:
        logger.error(f"Error in create_reminder: {e}", exc_info=True)
        return ToolResult(success=False, error_code="scheduler_error", message=str(e))


async def get_reminders(context: ToolExecutionContext, start_date: str, end_date: Optional[str] = None) -> ToolResult:
    """
    Retrieves active reminders for the current user between start_date and end_date.
    """
    s_date = parse_date_str(start_date) or get_today()
    e_date = parse_date_str(end_date) if end_date else s_date
    if not e_date:
        e_date = s_date

    try:
        rems = scheduler_service.get_reminders_for_date_range(s_date, e_date, chat_id=context.chat_id)
        return ToolResult(
            success=True,
            entity_type="reminder_list",
            data=rems,
            message=f"Найдено {len(rems)} активных напоминаний."
        )
    except Exception as e:
        logger.error(f"Error in get_reminders: {e}", exc_info=True)
        return ToolResult(success=False, error_code="scheduler_error", message=str(e))


async def search_reminders(context: ToolExecutionContext, query: str) -> ToolResult:
    """
    Searches active reminders matching query string for the current user.
    """
    if not query or not query.strip():
        return ToolResult(success=False, error_code="invalid_argument", message="Query cannot be empty.")

    try:
        rems = scheduler_service.search_reminders(query.strip(), chat_id=context.chat_id)
        return ToolResult(
            success=True,
            entity_type="reminder_list",
            data=rems,
            message=f"По запросу «{query}» найдено напоминаний: {len(rems)}."
        )
    except Exception as e:
        logger.error(f"Error in search_reminders: {e}", exc_info=True)
        return ToolResult(success=False, error_code="scheduler_error", message=str(e))


async def update_reminder(
    context: ToolExecutionContext,
    reminder_id: str,
    new_trigger_at: Optional[str] = None,
    new_message: Optional[str] = None
) -> ToolResult:
    """
    Updates an existing reminder's trigger time and/or message with user ownership check.
    """
    try:
        job = scheduler_service.scheduler.get_job(reminder_id)
        if not job or not job.args or job.args[0] != context.chat_id:
            return ToolResult(success=False, error_code="not_found", message=f"Напоминание #{reminder_id} не найдено.")

        target_dt = parse_datetime_str(new_trigger_at) if new_trigger_at else None
        ok = scheduler_service.update_reminder(
            reminder_id,
            new_trigger_at=target_dt,
            new_message=new_message.strip() if new_message else None
        )
        if ok:
            return ToolResult(
                success=True,
                entity_type="reminder",
                entity_id=reminder_id,
                message="Напоминание успешно обновлено."
            )
        return ToolResult(success=False, error_code="scheduler_error", message="Не удалось обновить напоминание.")
    except Exception as e:
        logger.error(f"Error in update_reminder: {e}", exc_info=True)
        return ToolResult(success=False, error_code="scheduler_error", message=str(e))


async def delete_reminder(context: ToolExecutionContext, reminder_id: str) -> ToolResult:
    """
    Cancels/removes a scheduled reminder by ID with user ownership check.
    """
    try:
        job = scheduler_service.scheduler.get_job(reminder_id)
        if not job or not job.args or job.args[0] != context.chat_id:
            return ToolResult(success=False, error_code="not_found", message=f"Напоминание #{reminder_id} не найдено.")

        msg = job.args[1] if len(job.args) > 1 else "Напоминание"
        ok = scheduler_service.remove_reminder(reminder_id)
        if ok:
            return ToolResult(
                success=True,
                entity_type="reminder",
                entity_id=reminder_id,
                message=f"Напоминание «{msg}» отменено."
            )
        return ToolResult(success=False, error_code="scheduler_error", message="Не удалось удалить напоминание.")
    except Exception as e:
        logger.error(f"Error in delete_reminder: {e}", exc_info=True)
        return ToolResult(success=False, error_code="scheduler_error", message=str(e))


# ---------------------------------------------------------------------------
# 3. GOOGLE CALENDAR TOOLS
# ---------------------------------------------------------------------------

async def create_calendar_event(
    context: ToolExecutionContext,
    title: str,
    start_time: str,
    end_time: Optional[str] = None,
    description: Optional[str] = None
) -> ToolResult:
    """
    Creates an event in Google Calendar.
    """
    if not calendar_service.is_configured():
        return ToolResult(
            success=False,
            error_code="calendar_not_configured",
            message="Интеграция Google Calendar не настроена (отсутствует service_account.json)."
        )

    if not title or not title.strip():
        return ToolResult(success=False, error_code="invalid_argument", message="Event title cannot be empty.")

    start_dt = parse_datetime_str(start_time)
    if not start_dt:
        return ToolResult(success=False, error_code="invalid_datetime", message=f"Не удалось распознать время начала «{start_time}».")

    end_dt = parse_datetime_str(end_time) if end_time else (start_dt + timedelta(hours=1))

    try:
        event = await calendar_service.create_event(
            title=title.strip(),
            start_time=start_dt,
            end_time=end_dt,
            description=description
        )
        if event:
            event_id = event.get("id")
            context_service.set_last_entity(context.user_id, "calendar", {
                "id": event_id,
                "title": title.strip(),
                "start_time": start_dt.isoformat()
            })
            context_service.set_last_date(context.user_id, start_dt.date())

            return ToolResult(
                success=True,
                entity_type="calendar",
                entity_id=event_id,
                data=event,
                message=f"Событие «{title}» добавлено в Google Календарь на {start_dt.strftime('%d.%m.%Y в %H:%M')}."
            )
        return ToolResult(success=False, error_code="calendar_error", message="Failed to create event in Google Calendar.")
    except Exception as e:
        logger.error(f"Error in create_calendar_event: {e}", exc_info=True)
        return ToolResult(success=False, error_code="calendar_error", message=str(e))


async def get_calendar_events(context: ToolExecutionContext, start_date: str, end_date: Optional[str] = None) -> ToolResult:
    """
    Retrieves Google Calendar events between start_date and end_date.
    """
    if not calendar_service.is_configured():
        return ToolResult(
            success=False,
            error_code="calendar_not_configured",
            message="Google Calendar не настроен."
        )

    s_date = parse_date_str(start_date) or get_today()
    e_date = parse_date_str(end_date) if end_date else s_date
    if not e_date:
        e_date = s_date

    try:
        events = await calendar_service.get_events_for_date_range(s_date, e_date)
        return ToolResult(
            success=True,
            entity_type="calendar_list",
            data=events,
            message=f"Найдено событий в календаре: {len(events)}."
        )
    except Exception as e:
        logger.error(f"Error in get_calendar_events: {e}", exc_info=True)
        return ToolResult(success=False, error_code="calendar_error", message=str(e))


async def search_calendar_events(context: ToolExecutionContext, query: str) -> ToolResult:
    """
    Searches Google Calendar events matching query string.
    """
    if not calendar_service.is_configured():
        return ToolResult(
            success=False,
            error_code="calendar_not_configured",
            message="Google Calendar не настроен."
        )

    try:
        events = await calendar_service.search_events(query.strip())
        return ToolResult(
            success=True,
            entity_type="calendar_list",
            data=events,
            message=f"В календаре по запросу «{query}» найдено: {len(events)} событий."
        )
    except Exception as e:
        logger.error(f"Error in search_calendar_events: {e}", exc_info=True)
        return ToolResult(success=False, error_code="calendar_error", message=str(e))


async def update_calendar_event(
    context: ToolExecutionContext,
    event_id: str,
    title: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    description: Optional[str] = None
) -> ToolResult:
    """
    Updates an existing Google Calendar event.
    """
    if not calendar_service.is_configured():
        return ToolResult(success=False, error_code="calendar_not_configured", message="Google Calendar не настроен.")

    start_dt = parse_datetime_str(start_time) if start_time else None
    end_dt = parse_datetime_str(end_time) if end_time else None

    try:
        event = await calendar_service.update_event(
            event_id=event_id,
            title=title,
            start_time=start_dt,
            end_time=end_dt,
            description=description
        )
        if event:
            return ToolResult(success=True, entity_type="calendar", entity_id=event_id, data=event, message="Событие в календаре обновлено.")
        return ToolResult(success=False, error_code="not_found", message=f"Событие {event_id} не найдено.")
    except Exception as e:
        logger.error(f"Error in update_calendar_event: {e}", exc_info=True)
        return ToolResult(success=False, error_code="calendar_error", message=str(e))


async def delete_calendar_event(context: ToolExecutionContext, event_id: str) -> ToolResult:
    """
    Deletes a Google Calendar event by ID.
    """
    if not calendar_service.is_configured():
        return ToolResult(success=False, error_code="calendar_not_configured", message="Google Calendar не настроен.")

    try:
        ok = await calendar_service.delete_event(event_id)
        if ok:
            return ToolResult(success=True, entity_type="calendar", entity_id=event_id, message="Событие в календаре удалено.")
        return ToolResult(success=False, error_code="calendar_error", message="Не удалось удалить событие.")
    except Exception as e:
        logger.error(f"Error in delete_calendar_event: {e}", exc_info=True)
        return ToolResult(success=False, error_code="calendar_error", message=str(e))


# ---------------------------------------------------------------------------
# 4. NOTES & OBSIDIAN TOOLS
# ---------------------------------------------------------------------------

async def create_note(context: ToolExecutionContext, content: str, folder_name: Optional[str] = None) -> ToolResult:
    """
    Saves a quick note, fact, password, or memo.
    """
    if not content or not content.strip():
        return ToolResult(success=False, error_code="invalid_argument", message="Note content cannot be empty.")

    clean_content = content.strip()
    folder_id = None
    if folder_name and folder_name.strip():
        folders = await notes_service.get_folders(context.user_id)
        for f in folders:
            if f["name"].lower() == folder_name.strip().lower():
                folder_id = f["id"]
                break
        if not folder_id:
            folder_id = await notes_service.create_folder(context.user_id, folder_name.strip())

    try:
        note_id = await notes_service.add_note(context.user_id, clean_content, folder_id=folder_id)
        context_service.set_last_entity(context.user_id, "note", {
            "id": note_id,
            "content": clean_content
        })
        return ToolResult(
            success=True,
            entity_type="note",
            entity_id=note_id,
            data={"id": note_id, "content": clean_content, "folder_id": folder_id},
            message=f"Заметка сохранена (ID #{note_id})."
        )
    except Exception as e:
        logger.error(f"Error in create_note: {e}", exc_info=True)
        return ToolResult(success=False, error_code="db_error", message=str(e))


async def get_notes(context: ToolExecutionContext, folder_id: Optional[int] = None) -> ToolResult:
    """
    Retrieves saved notes for user.
    """
    try:
        notes = await notes_service.get_notes(context.user_id, folder_id=folder_id)
        return ToolResult(
            success=True,
            entity_type="note_list",
            data=notes,
            message=f"Найдено заметок: {len(notes)}."
        )
    except Exception as e:
        logger.error(f"Error in get_notes: {e}", exc_info=True)
        return ToolResult(success=False, error_code="db_error", message=str(e))


async def search_notes(context: ToolExecutionContext, query: str) -> ToolResult:
    """
    Searches notes by text/keywords.
    """
    if not query or not query.strip():
        return ToolResult(success=False, error_code="invalid_argument", message="Query cannot be empty.")

    try:
        notes = await notes_service.search_notes(context.user_id, query.strip())
        return ToolResult(
            success=True,
            entity_type="note_list",
            data=notes,
            message=f"По запросу «{query}» найдено заметок: {len(notes)}."
        )
    except Exception as e:
        logger.error(f"Error in search_notes: {e}", exc_info=True)
        return ToolResult(success=False, error_code="db_error", message=str(e))


async def update_note(context: ToolExecutionContext, note_id: int, new_content: str) -> ToolResult:
    """
    Updates the content of an existing note by ID.
    """
    if not new_content or not new_content.strip():
        return ToolResult(success=False, error_code="invalid_argument", message="New content cannot be empty.")

    try:
        ok = await notes_service.update_note(note_id, context.user_id, new_content.strip())
        if ok:
            return ToolResult(success=True, entity_type="note", entity_id=note_id, message="Заметка обновлена.")
        return ToolResult(success=False, error_code="not_found", message=f"Заметка #{note_id} не найдена.")
    except Exception as e:
        logger.error(f"Error in update_note: {e}", exc_info=True)
        return ToolResult(success=False, error_code="db_error", message=str(e))


async def delete_note(context: ToolExecutionContext, note_id: int) -> ToolResult:
    """
    Deletes a note by ID.
    """
    try:
        ok = await notes_service.delete_note(note_id, context.user_id)
        if ok:
            return ToolResult(success=True, entity_type="note", entity_id=note_id, message="Заметка удалена.")
        return ToolResult(success=False, error_code="not_found", message=f"Заметка #{note_id} не найдена.")
    except Exception as e:
        logger.error(f"Error in delete_note: {e}", exc_info=True)
        return ToolResult(success=False, error_code="db_error", message=str(e))


async def append_obsidian_daily_note(
    context: ToolExecutionContext,
    task_text: str,
    target_date: Optional[str] = None,
    target_section: Optional[str] = None
) -> ToolResult:
    """
    Appends a task item to the Obsidian daily note on WebDAV.
    """
    if not obsidian_service.is_configured():
        return ToolResult(success=False, error_code="obsidian_not_configured", message="Obsidian WebDAV не настроен.")

    t_date = parse_date_str(target_date) if target_date else get_today()
    sec = target_section or "## Задачи на сегодня"

    try:
        path = await obsidian_service.add_task_to_daily_note(
            task_text=task_text.strip(),
            target_date=t_date,
            target_section=sec
        )
        if path:
            return ToolResult(success=True, entity_type="obsidian", data={"path": path}, message=f"Заметка добавлена в Obsidian (`{path}`).")
        return ToolResult(success=False, error_code="obsidian_error", message="Failed to sync with Obsidian.")
    except Exception as e:
        logger.error(f"Error in append_obsidian_daily_note: {e}", exc_info=True)
        return ToolResult(success=False, error_code="obsidian_error", message=str(e))


# ---------------------------------------------------------------------------
# 5. GOALS TOOLS
# ---------------------------------------------------------------------------

async def create_goal(context: ToolExecutionContext, text: str, target_month: Optional[str] = None) -> ToolResult:
    """
    Creates a monthly goal for user for target_month (YYYY-MM, defaults to current month).
    """
    if not text or not text.strip():
        return ToolResult(success=False, error_code="invalid_argument", message="Goal text cannot be empty.")

    clean_text = text.strip()
    m_str = target_month or get_today().strftime("%Y-%m")

    try:
        goal_id = await goals_service.add_goal(context.user_id, clean_text, m_str)
        return ToolResult(
            success=True,
            entity_type="goal",
            entity_id=goal_id,
            data={"id": goal_id, "text": clean_text, "month": m_str},
            message=f"Цель «{clean_text}» сохранена на месяц {m_str}."
        )
    except Exception as e:
        logger.error(f"Error in create_goal: {e}", exc_info=True)
        return ToolResult(success=False, error_code="db_error", message=str(e))


async def get_goals(context: ToolExecutionContext, target_month: Optional[str] = None) -> ToolResult:
    """
    Retrieves monthly goals for user.
    """
    m_str = target_month or get_today().strftime("%Y-%m")
    try:
        goals = await goals_service.get_goals(context.user_id, m_str)
        return ToolResult(
            success=True,
            entity_type="goal_list",
            data=goals,
            message=f"Найдено целей на {m_str}: {len(goals)}."
        )
    except Exception as e:
        logger.error(f"Error in get_goals: {e}", exc_info=True)
        return ToolResult(success=False, error_code="db_error", message=str(e))


async def complete_goal(context: ToolExecutionContext, goal_id: int) -> ToolResult:
    """
    Toggles/marks a monthly goal as completed.
    """
    try:
        new_state = await goals_service.toggle_goal(goal_id, context.user_id)
        if new_state is not None:
            status_text = "выполнена" if new_state else "снята с выполнения"
            return ToolResult(
                success=True,
                entity_type="goal",
                entity_id=goal_id,
                message=f"Цель #{goal_id} {status_text}."
            )
        return ToolResult(success=False, error_code="not_found", message=f"Цель #{goal_id} не найдена.")
    except Exception as e:
        logger.error(f"Error in complete_goal: {e}", exc_info=True)
        return ToolResult(success=False, error_code="db_error", message=str(e))


async def delete_goal(context: ToolExecutionContext, goal_id: int) -> ToolResult:
    """
    Deletes a monthly goal by ID.
    """
    try:
        ok = await goals_service.delete_goal(goal_id, context.user_id)
        if ok:
            return ToolResult(success=True, entity_type="goal", entity_id=goal_id, message=f"Цель #{goal_id} удалена.")
        return ToolResult(success=False, error_code="not_found", message=f"Цель #{goal_id} не найдена.")
    except Exception as e:
        logger.error(f"Error in delete_goal: {e}", exc_info=True)
        return ToolResult(success=False, error_code="db_error", message=str(e))


# ---------------------------------------------------------------------------
# 6. RECURRING & HABITS TOOLS
# ---------------------------------------------------------------------------

async def create_recurring_task(
    context: ToolExecutionContext,
    title: str,
    repeat_type: str,
    repeat_days: Optional[List[str]] = None,
    interval: Optional[int] = None,
    time_of_day: Optional[str] = None
) -> ToolResult:
    """
    Creates a recurring habit or recurring task and registers an APScheduler trigger.
    repeat_type: 'daily', 'weekly', 'interval_days', 'interval_hours', 'interval_minutes'
    repeat_days: list like ['mon', 'wed', 'fri']
    interval: integer e.g. 4 for every 4 days
    time_of_day: 'HH:MM' e.g. '09:00'
    """
    if not title or not title.strip():
        return ToolResult(success=False, error_code="invalid_argument", message="Title cannot be empty.")

    clean_title = title.strip()
    r_type = repeat_type or "daily"
    cron_expr = ",".join(repeat_days) if repeat_days else None
    t_time = time_of_day or "10:00"

    try:
        task_id = await recurring_service.add_recurring_task(
            user_id=context.user_id,
            title=clean_title,
            repeat_type=r_type,
            cron_expression=cron_expr,
            interval_days=interval,
            target_time=t_time
        )
        if task_id > 0:
            task = await recurring_service.get_task_by_id(task_id, context.user_id)
            if task:
                scheduler_service.schedule_recurring_task_job(task)

            return ToolResult(
                success=True,
                entity_type="recurring",
                entity_id=task_id,
                data=task,
                message=f"Привычка/повтор «{clean_title}» создана и запланирована на {t_time}."
            )
        return ToolResult(success=False, error_code="db_error", message="Failed to save recurring task.")
    except Exception as e:
        logger.error(f"Error in create_recurring_task: {e}", exc_info=True)
        return ToolResult(success=False, error_code="db_error", message=str(e))


async def get_recurring_tasks(context: ToolExecutionContext) -> ToolResult:
    """
    Retrieves active habits and recurring tasks for the user.
    """
    try:
        tasks = await recurring_service.get_user_tasks(context.user_id)
        return ToolResult(
            success=True,
            entity_type="recurring_list",
            data=tasks,
            message=f"Найдено повторяющихся задач/привычек: {len(tasks)}."
        )
    except Exception as e:
        logger.error(f"Error in get_recurring_tasks: {e}", exc_info=True)
        return ToolResult(success=False, error_code="db_error", message=str(e))


async def delete_recurring_task(context: ToolExecutionContext, task_id: int) -> ToolResult:
    """
    Deletes a recurring task / habit and removes its schedule.
    """
    try:
        task = await recurring_service.get_task_by_id(task_id, context.user_id)
        if not task:
            return ToolResult(success=False, error_code="not_found", message=f"Привычка #{task_id} не найдена.")

        ok = await recurring_service.delete_task(task_id, context.user_id)
        if ok:
            job_id = f"recurring_{context.user_id}_{task_id}"
            scheduler_service.remove_reminder(job_id)
            return ToolResult(success=True, entity_type="recurring", entity_id=task_id, message=f"Привычка «{task['title']}» удалена.")
        return ToolResult(success=False, error_code="db_error", message="Failed to delete recurring task.")
    except Exception as e:
        logger.error(f"Error in delete_recurring_task: {e}", exc_info=True)
        return ToolResult(success=False, error_code="db_error", message=str(e))


# ---------------------------------------------------------------------------
# 7. HIGH-LEVEL COMPOSITE TOOLS
# ---------------------------------------------------------------------------

async def get_schedule(context: ToolExecutionContext, start_date: str, end_date: Optional[str] = None) -> ToolResult:
    """
    High-level tool that aggregates all tasks, reminders, and Google Calendar events
    for the requested date range into a single structured overview.
    """
    s_date = parse_date_str(start_date) or get_today()
    e_date = parse_date_str(end_date) if end_date else s_date
    if not e_date:
        e_date = s_date

    context_service.set_last_date(context.user_id, s_date)

    tasks = await tasks_service.get_tasks_for_date_range(context.user_id, s_date, e_date)
    reminders = scheduler_service.get_reminders_for_date_range(s_date, e_date, chat_id=context.chat_id)
    cal_events = []
    if calendar_service.is_configured():
        cal_events = await calendar_service.get_events_for_date_range(s_date, e_date)

    combined_data = {
        "start_date": s_date.strftime("%Y-%m-%d"),
        "end_date": e_date.strftime("%Y-%m-%d"),
        "tasks": tasks,
        "reminders": reminders,
        "calendar_events": [
            {
                "id": ev.get("id"),
                "summary": ev.get("summary", "Без названия"),
                "start": ev.get("start", {}).get("dateTime", "") or ev.get("start", {}).get("date", ""),
                "description": ev.get("description", "")
            }
            for ev in cal_events
        ]
    }

    return ToolResult(
        success=True,
        entity_type="schedule",
        data=combined_data,
        message=f"Расписание с {s_date.strftime('%d.%m.%Y')} по {e_date.strftime('%d.%m.%Y')}: {len(tasks)} задач, {len(reminders)} напоминаний, {len(cal_events)} событий календаря."
    )


async def search_everything(
    context: ToolExecutionContext,
    query: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
) -> ToolResult:
    """
    High-level tool that searches across tasks, reminders, calendar events, and notes.
    Returns structured records with type, id, text, date, and time.
    """
    if not query or not query.strip():
        return ToolResult(success=False, error_code="invalid_argument", message="Query cannot be empty.")

    clean_q = query.strip()
    results = []

    # 1. Search tasks
    found_tasks = await tasks_service.search_tasks(context.user_id, clean_q)
    for t in found_tasks:
        results.append({
            "type": "task",
            "id": t["id"],
            "text": t["task_text"],
            "date": t["target_date"],
            "time": None,
            "is_completed": bool(t.get("is_completed"))
        })

    # 2. Search reminders
    found_rems = scheduler_service.search_reminders(clean_q, chat_id=context.chat_id)
    for r in found_rems:
        results.append({
            "type": "reminder",
            "id": r["id"],
            "text": r["message"],
            "date": r["date"],
            "time": r["time"]
        })

    # 3. Search calendar
    if calendar_service.is_configured():
        found_events = await calendar_service.search_events(clean_q)
        for ev in found_events:
            start_str = ev.get("start", {}).get("dateTime", "") or ev.get("start", {}).get("date", "")
            results.append({
                "type": "calendar",
                "id": ev.get("id"),
                "text": ev.get("summary", "Без названия"),
                "date": start_str[:10] if len(start_str) >= 10 else None,
                "time": start_str[11:16] if len(start_str) >= 16 else None
            })

    # 4. Search notes
    found_notes = await notes_service.search_notes(context.user_id, clean_q)
    for n in found_notes:
        results.append({
            "type": "note",
            "id": n["id"],
            "text": n["content"],
            "date": n.get("created_at", "")[:10] if n.get("created_at") else None,
            "time": None
        })

    return ToolResult(
        success=True,
        entity_type="search",
        data=results,
        message=f"По запросу «{clean_q}» найдено сущностей: {len(results)}."
    )
