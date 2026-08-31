import os
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime
from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from app.config import settings
from app.utils.timezone import get_now, get_tz

logger = logging.getLogger(__name__)


def get_reminder_inline_keyboard(snooze_minutes: Optional[int] = None) -> InlineKeyboardMarkup:
    if snooze_minutes is None:
        snooze_minutes = getattr(settings, "DEFAULT_SNOOZE_MINUTES", 5)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=f"⏳ Отложить на {snooze_minutes} мин",
                callback_data="snooze:def"
            )
        ],
        [
            InlineKeyboardButton(
                text="⏱ Отложить на N мин",
                callback_data="snooze:rel"
            )
        ],
        [
            InlineKeyboardButton(
                text="🕒 На какое время отложить",
                callback_data="snooze:abs"
            )
        ]
    ])
    return keyboard


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
        keyboard = get_reminder_inline_keyboard()
        try:
            await bot.send_message(chat_id=chat_id, text=formatted_msg, reply_markup=keyboard)
        except Exception:
            await bot.send_message(
                chat_id=chat_id,
                text=f"⏰ Напоминание:\n\n{message_text}",
                reply_markup=keyboard,
                parse_mode=None
            )
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
            return res_list
        except Exception as e:
            logger.error(f"Error searching reminders for query '{query}': {e}")
            return []

    def setup_periodic_schedule_summary(self, chat_id: int, interval_hours: int):
        """
        Schedules or cancels periodic schedule summary job for user.
        If interval_hours > 0, schedules job every N hours.
        """
        job_id = f"periodic_sched_{chat_id}"
        try:
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)
                logger.info(f"Removed old periodic schedule summary job {job_id}")

            if interval_hours > 0:
                from apscheduler.triggers.interval import IntervalTrigger
                trigger = IntervalTrigger(hours=interval_hours, timezone=get_tz())
                self.scheduler.add_job(
                    send_periodic_schedule_summary,
                    trigger=trigger,
                    id=job_id,
                    args=[chat_id],
                    replace_existing=True
                )
                logger.info(f"Scheduled periodic schedule summary for chat {chat_id} every {interval_hours} hours.")
        except Exception as e:
            logger.error(f"Failed to setup periodic schedule summary for chat {chat_id}: {e}")


async def send_periodic_schedule_summary(chat_id: int):
    """
    Callback function executed by APScheduler periodically to send current day schedule summary.
    """
    from app.handlers.text import render_schedule_view
    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN)
    )
    try:
        today = get_today()
        text, reply_markup = await render_schedule_view(chat_id, today)
        formatted_msg = f"🔔 **Авто-напоминание планов на сегодня:**\n\n{text}"
        try:
            await bot.send_message(chat_id=chat_id, text=formatted_msg, reply_markup=reply_markup)
        except Exception:
            await bot.send_message(chat_id=chat_id, text=formatted_msg, reply_markup=reply_markup, parse_mode=None)
    except Exception as e:
        logger.error(f"Error sending periodic schedule summary: {e}", exc_info=True)
    finally:
        await bot.session.close()


scheduler_service = SchedulerService()
