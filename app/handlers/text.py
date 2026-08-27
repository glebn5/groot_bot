import logging
from aiogram import Router, Bot, F
from aiogram.enums import ChatAction
from aiogram.types import Message

from app.models.schemas import ParsedAction
from app.services.llm import llm_service
from app.services.obsidian import obsidian_service
from app.services.calendar import calendar_service
from app.services.scheduler import scheduler_service

logger = logging.getLogger(__name__)
router = Router(name="text")


async def execute_action_pipeline(bot: Bot, chat_id: int, action: ParsedAction) -> str:
    """
    Executes tasks, calendar events, and scheduled reminders according to ParsedAction.
    Returns status details to append to user confirmation message.
    """
    status_notes = []

    # 1. Sync task with Obsidian WebDAV
    if action.obsidian_entry:
        try:
            path = await obsidian_service.add_task_to_daily_note(
                task_text=action.obsidian_entry.task_text,
                target_date=action.obsidian_entry.entry_date,
                target_section=action.obsidian_entry.target_section
            )
            status_notes.append(f"📝 Задача добавлена в Obsidian (`{path}`)")
        except Exception as e:
            logger.error(f"Obsidian sync failed: {e}")
            status_notes.append(f"⚠️ Ошибка синхронизации Obsidian: {e}")

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
                    bot=bot,
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


@router.message(F.text & ~F.text.startswith("/"))
async def handle_text_message(message: Message, bot: Bot):
    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    try:
        parsed_action = await llm_service.parse_user_request(text_content=message.text)
        reply = await execute_action_pipeline(bot, message.chat.id, parsed_action)
        await message.answer(reply, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error handling text message: {e}", exc_info=True)
        await message.answer(f"❌ Произошла ошибка при обработке запроса: {str(e)}")
