import logging
from datetime import date, datetime
from aiogram import Router, Bot, F
from aiogram.enums import ChatAction
from aiogram.types import Message

from app.models.schemas import ParsedAction
from app.services.llm import llm_service
from app.services.obsidian import obsidian_service
from app.services.calendar import calendar_service
from app.services.scheduler import scheduler_service
from app.services.notes import notes_service
from app.services.tasks import tasks_service
from app.services.context import context_service
from app.handlers.notes import render_notes_view
from app.utils.timezone import get_today

logger = logging.getLogger(__name__)
router = Router(name="text")


async def execute_action_pipeline(bot: Bot, chat_id: int, action: ParsedAction) -> str:
    """
    Executes tasks, calendar events, and scheduled reminders according to ParsedAction.
    Returns status details to append to user confirmation message.
    """
    # 0. Handle Note Queries ("покажи заметки", "какие у меня заметки")
    if action.is_note_query:
        text, _ = await render_notes_view(chat_id)
        return text

    # 0. Handle Task Rescheduling/Moving ("перемести задачу на 28 число")
    if action.is_task_move and action.move_to_date:
        moved = await tasks_service.move_task(
            user_id=chat_id,
            query=action.move_task_query or "",
            to_date=action.move_to_date,
            from_date=action.move_from_date
        )
        if moved:
            old_str = datetime.strptime(moved['old_date'], "%Y-%m-%d").strftime("%d.%m.%Y")
            new_str = datetime.strptime(moved['new_date'], "%Y-%m-%d").strftime("%d.%m.%Y")
            return f"🌴 **Готово!** Перенёс задачу **«{moved['task_text']}»** с {old_str} на {new_str} ✨"

    # 0. Handle Task Clearing ("убери все задачи", "очисти задачи")
    if action.is_task_clear:
        target_clear_date = action.clear_date or context_service.get_last_date(chat_id)
        count = await tasks_service.clear_tasks_for_date(chat_id, target_clear_date)
        if target_clear_date:
            d_str = target_clear_date.strftime("%d.%m.%Y")
            return f"🌴 Все задачи на {d_str} успешно удалены (всего: {count}) 🗑"
        else:
            return f"🌴 Все ваши задачи успешно удалены (всего: {count}) 🗑"

    # 0. Handle Single Task Deletion ("удали задачу...")
    if action.is_task_delete_single and action.delete_task_query:
        target_del_date = action.task_date or context_service.get_last_date(chat_id)
        deleted = await tasks_service.delete_task_by_query(chat_id, action.delete_task_query, target_del_date)
        if deleted:
            return f"🌴 Задача **«{action.delete_task_query}»** успешно удалена 🗑"
        else:
            return f"⚠️ Задача по запросу «{action.delete_task_query}» не найдена."

    # 0. Handle Schedule Queries (e.g. "какие планы на сегодня", "планы на август", "что 3 сентября")
    if action.is_schedule_query and action.query_date:
        start_date = action.query_date
        end_date = action.query_end_date or start_date

        # Remember target_date in user conversation context memory
        context_service.set_last_date(chat_id, start_date)

        schedule_lines = []

        if start_date == end_date:
            header_str = f"🌴 **Привет! Вот твои планы на {start_date.strftime('%d.%m.%Y')}:**\n\n"
        else:
            header_str = f"🌴 **Привет! Вот твои планы с {start_date.strftime('%d.%m.%Y')} по {end_date.strftime('%d.%m.%Y')}:**\n\n"

        # 1) Get local tasks for date range
        local_tasks = await tasks_service.get_tasks_for_date_range(chat_id, start_date, end_date)
        if local_tasks:
            schedule_lines.append("✨ **Что нужно сделать:**")
            for t in local_tasks:
                status_icon = "✅" if t.get("is_completed") else "▫️"
                t_date_str = datetime.strptime(t['target_date'], "%Y-%m-%d").strftime("%d.%m")
                if start_date == end_date:
                    schedule_lines.append(f"  {status_icon} {t['task_text']}")
                else:
                    schedule_lines.append(f"  {status_icon} [{t_date_str}] {t['task_text']}")

        # 2) Get APScheduler reminders for date range
        reminders = scheduler_service.get_reminders_for_date_range(start_date, end_date, chat_id=chat_id)
        if reminders:
            if schedule_lines:
                schedule_lines.append("")
            schedule_lines.append("🔔 **Не забудь:**")
            for r in reminders:
                if start_date == end_date:
                    schedule_lines.append(f"  • {r['time']} — {r['message']}")
                else:
                    schedule_lines.append(f"  • {r['date']} в {r['time']} — {r['message']}")

        # 3) Get Google Calendar events for date range
        events = await calendar_service.get_events_for_date_range(start_date, end_date)
        if events:
            if schedule_lines:
                schedule_lines.append("")
            schedule_lines.append("📅 **В календаре:**")
            for ev in events:
                summary = ev.get('summary', 'Без названия')
                start_dt = ev.get('start', {}).get('dateTime', '')
                time_part = start_dt[11:16] if len(start_dt) >= 16 else "Весь день"
                date_part = start_dt[:10] if len(start_dt) >= 10 else ""
                if start_date == end_date:
                    schedule_lines.append(f"  • {time_part} — {summary}")
                else:
                    schedule_lines.append(f"  • {date_part} {time_part} — {summary}")

        # 4) Get Obsidian tasks
        obs_tasks = await obsidian_service.get_daily_tasks(start_date)
        if obs_tasks:
            if schedule_lines:
                schedule_lines.append("")
            schedule_lines.append("📝 **В Obsidian:**")
            for t in obs_tasks:
                schedule_lines.append(f"  {t}")

        if not schedule_lines:
            if start_date == end_date:
                return f"🌴 **Привет! На {start_date.strftime('%d.%m.%Y')} планов пока нет.**\nВсё свободно! Вы отдыхаете или хотите добавить новую задачу?"
            else:
                return f"🌴 **Привет! С {start_date.strftime('%d.%m.%Y')} по {end_date.strftime('%d.%m.%Y')} планов пока нет.**\nВсё свободно!"

        return header_str + "\n".join(schedule_lines)

    status_notes = []

    # 0. Save Task if requested for a date
    if action.is_task_add and action.task_text:
        try:
            t_date = action.task_date or get_today()
            await tasks_service.add_task(user_id=chat_id, task_text=action.task_text, target_date=t_date)
            d_str = t_date.strftime("%d.%m.%Y")
            status_notes.append(f"📋 Задача «{action.task_text}» сохранена на {d_str}!")
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
        for r in action.reminders:
            try:
                scheduler_service.schedule_reminder(
                    chat_id=chat_id,
                    trigger_at=r.trigger_at,
                    message=r.message
                )
                time_str = r.trigger_at.strftime("%d.%m.%Y в %H:%M")
                status_notes.append(f"⏰ Запланировано напоминание на {time_str}")
            except Exception as e:
                logger.error(f"Scheduling reminder failed: {e}")
                status_notes.append(f"⚠️ Ошибка планирования напоминания: {e}")

    response_text = action.confirmation_text
    if status_notes:
        response_text += "\n\n" + "\n".join(status_notes)

    return response_text


async def safe_answer_markdown(message: Message, text: str, reply_markup=None):
    try:
        await message.answer(text, reply_markup=reply_markup, parse_mode="Markdown")
    except Exception as e:
        logger.warning(f"Failed to send with Markdown parse_mode: {e}. Falling back to plain text.")
        await message.answer(text, reply_markup=reply_markup, parse_mode=None)


@router.message(F.text & ~F.text.startswith("/"))
async def handle_text_message(message: Message, bot: Bot):
    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    try:
        ctx_date = context_service.get_last_date(message.chat.id)
        parsed_action = await llm_service.parse_user_request(text_content=message.text, context_date=ctx_date)
        reply = await execute_action_pipeline(bot, message.chat.id, parsed_action)
        await safe_answer_markdown(message, reply)
    except Exception as e:
        logger.error(f"Error handling text message: {e}", exc_info=True)
        await message.answer(f"❌ Произошла ошибка при обработке запроса: {str(e)}", parse_mode=None)
