import os
import logging
from datetime import datetime
from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from app.config import settings
from app.utils.timezone import get_now, get_tz

logger = logging.getLogger(__name__)


async def send_reminder_notification(chat_id: int, message_text: str):
    """
    Callback function executed by APScheduler when trigger_at is reached.
    Creates a temporary Bot instance to avoid pickling issues in SQLite jobstore.
    """
    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN)
    )
    try:
        logger.info(f"Sending scheduled reminder to chat_id={chat_id}: '{message_text}'")
        formatted_msg = f"⏰ **Напоминание:**\n\n{message_text}"
        await bot.send_message(chat_id=chat_id, text=formatted_msg)
    except Exception as e:
        logger.error(f"Error sending scheduled reminder notification: {e}", exc_info=True)
    finally:
        await bot.session.close()


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
        self.scheduler = AsyncIOScheduler(jobstores=jobstores, timezone=get_tz())

    def start(self):
        if not self.scheduler.running:
            self.scheduler.start()
            logger.info(f"APScheduler initialized and started successfully with timezone {settings.TIMEZONE}.")

    def stop(self):
        if self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("APScheduler shutdown successfully.")

    def schedule_reminder(self, chat_id: int, trigger_at: datetime, message: str):
        """
        Schedules a one-off reminder notification to be sent to chat_id at trigger_at.
        """
        tz = get_tz()
        if trigger_at.tzinfo is None:
            trigger_at = trigger_at.replace(tzinfo=tz)
        else:
            trigger_at = trigger_at.astimezone(tz)

        now = get_now()
        if trigger_at <= now:
            logger.warning(f"Reminder trigger time {trigger_at} is in the past. Adjusting to execute immediately.")
            trigger_at = now

        job = self.scheduler.add_job(
            send_reminder_notification,
            'date',
            run_date=trigger_at,
            args=[chat_id, message],
            misfire_grace_time=300
        )
        logger.info(f"Scheduled reminder (Job ID={job.id}) for chat_id={chat_id} at {trigger_at}")
        return job.id

    def get_reminders_for_date(self, target_date, chat_id=None) -> list:
        """
        Returns list of active reminders for a specific date: [{'time': '14:15', 'message': '...'}]
        """
        return self.get_reminders_for_date_range(target_date, target_date, chat_id=chat_id)

    def get_reminders_for_date_range(self, start_date, end_date, chat_id=None) -> list:
        """
        Returns list of active reminders between start_date and end_date.
        """
        results = []
        if not self.scheduler:
            return results
        try:
            for job in self.scheduler.get_jobs():
                if job.next_run_time and start_date <= job.next_run_time.date() <= end_date:
                    if chat_id is None or (job.args and len(job.args) > 0 and job.args[0] == chat_id):
                        results.append({
                            "date": job.next_run_time.strftime("%d.%m.%Y"),
                            "time": job.next_run_time.strftime("%H:%M"),
                            "message": job.args[1] if len(job.args) > 1 else "Напоминание"
                        })
            results.sort(key=lambda x: (x["date"], x["time"]))
        except Exception as e:
            logger.error(f"Error fetching reminders for range {start_date} to {end_date}: {e}")
        return results


scheduler_service = SchedulerService()
