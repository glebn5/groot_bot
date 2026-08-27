import os
import logging
from datetime import datetime
from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from app.config import settings

logger = logging.getLogger(__name__)


async def send_reminder_notification(bot: Bot, chat_id: int, message_text: str):
    """
    Callback function executed by APScheduler when trigger_at is reached.
    """
    try:
        logger.info(f"Sending scheduled reminder to chat_id={chat_id}: '{message_text}'")
        formatted_msg = f"⏰ **Напоминание:**\n\n{message_text}"
        await bot.send_message(chat_id=chat_id, text=formatted_msg, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error sending scheduled reminder notification: {e}", exc_info=True)


class SchedulerService:
    def __init__(self):
        # Ensure database directory exists
        db_path = settings.DATABASE_PATH
        db_dir = os.path.dirname(db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)

        jobstores = {
            'default': SQLAlchemyJobStore(url=f"sqlite:///{db_path}")
        }
        self.scheduler = AsyncIOScheduler(jobstores=jobstores)

    def start(self):
        if not self.scheduler.running:
            self.scheduler.start()
            logger.info("APScheduler initialized and started successfully.")

    def stop(self):
        if self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("APScheduler shutdown successfully.")

    def schedule_reminder(self, bot: Bot, chat_id: int, trigger_at: datetime, message: str):
        """
        Schedules a one-off reminder notification to be sent to chat_id at trigger_at.
        """
        now = datetime.now()
        if trigger_at <= now:
            logger.warning(f"Reminder trigger time {trigger_at} is in the past. Adjusting to execute immediately.")
            trigger_at = now

        job = self.scheduler.add_job(
            send_reminder_notification,
            'date',
            run_date=trigger_at,
            args=[bot, chat_id, message],
            misfire_grace_time=300
        )
        logger.info(f"Scheduled reminder (Job ID={job.id}) for chat_id={chat_id} at {trigger_at}")
        return job.id


scheduler_service = SchedulerService()
