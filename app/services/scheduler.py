import os
import logging
from typing import Optional, List, Dict, Any
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
        Returns list of active reminders between start_date and end_date in the configured timezone.
        """
        results = []
        if not self.scheduler:
            return results
        tz = get_tz()
        try:
            for job in self.scheduler.get_jobs():
                if job.next_run_time:
                    run_dt = job.next_run_time.astimezone(tz)
                    if start_date <= run_dt.date() <= end_date:
                        if chat_id is None or (job.args and len(job.args) > 0 and job.args[0] == chat_id):
                            results.append({
                                "id": str(job.id),
                                "date": run_dt.strftime("%d.%m.%Y"),
                                "time": run_dt.strftime("%H:%M"),
                                "message": job.args[1] if len(job.args) > 1 else "Напоминание"
                            })
            results.sort(key=lambda x: (x["date"], x["time"]))
        except Exception as e:
            logger.error(f"Error fetching reminders for range {start_date} to {end_date}: {e}")
        return results

    def remove_reminder(self, job_id: str) -> bool:
        """
        Removes a scheduled reminder job by ID.
        """
        try:
            self.scheduler.remove_job(job_id)
            logger.info(f"Successfully removed reminder job {job_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to remove reminder job {job_id}: {e}")
            return False

    def update_reminder(self, job_id: str, new_trigger_at: Optional[datetime] = None, new_message: Optional[str] = None) -> bool:
        """
        Updates an existing reminder job's trigger time and/or message.
        """
        try:
            job = self.scheduler.get_job(job_id)
            if not job:
                return False

            tz = get_tz()
            kwargs = {}
            if new_trigger_at:
                if new_trigger_at.tzinfo is None:
                    new_trigger_at = new_trigger_at.replace(tzinfo=tz)
                else:
                    new_trigger_at = new_trigger_at.astimezone(tz)
                kwargs['next_run_time'] = new_trigger_at

            new_args = list(job.args)
            if new_message and len(new_args) > 1:
                new_args[1] = new_message
                kwargs['args'] = new_args

            if kwargs:
                self.scheduler.modify_job(job_id, **kwargs)
                logger.info(f"Updated reminder job {job_id} with kwargs: {kwargs}")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to update reminder job {job_id}: {e}")
            return False

    def search_reminders(self, query: str, chat_id=None) -> list:
        """
        Searches active reminders matching query keywords.
        """
        results = {}
        if not self.scheduler:
            return list(results.values())
        tz = get_tz()
        stop_words = {"завтра", "сегодня", "вчера", "когда", "где", "во", "сколько", "напомни", "планы", "на", "для", "что", "есть"}
        words = [w.lower().strip("?!.,") for w in query.split() if len(w.strip("?!.,")) >= 2 and w.lower().strip("?!.,") not in stop_words]
        if not words:
            words = [query.lower().strip("?!.,")]

        try:
            for job in self.scheduler.get_jobs():
                if job.next_run_time:
                    msg = (job.args[1] if len(job.args) > 1 else "").lower()
                    if any(w in msg for w in words):
                        if chat_id is None or (job.args and len(job.args) > 0 and job.args[0] == chat_id):
                            run_dt = job.next_run_time.astimezone(tz)
                            results[str(job.id)] = {
                                "id": str(job.id),
                                "date": run_dt.strftime("%d.%m.%Y"),
                                "time": run_dt.strftime("%H:%M"),
                                "message": job.args[1] if len(job.args) > 1 else "Напоминание"
                            }
            res_list = list(results.values())
            res_list.sort(key=lambda x: (x["date"], x["time"]))
            return res_list
        except Exception as e:
            logger.error(f"Error searching reminders for query '{query}': {e}")
            return []


scheduler_service = SchedulerService()
