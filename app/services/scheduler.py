import os
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
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


def is_in_quiet_hours() -> bool:
    """
    Checks if current time falls within quiet hours (Do Not Disturb interval).
    If ALLOW_NIGHT_NOTIFICATIONS is True, quiet hours are disabled (returns False).
    """
    if getattr(settings, "ALLOW_NIGHT_NOTIFICATIONS", False):
        return False

    now_time = get_now().time()
    start_str = getattr(settings, "QUIET_HOURS_START", "23:00")
    end_str = getattr(settings, "QUIET_HOURS_END", "08:00")

    try:
        start_h, start_m = map(int, start_str.split(":"))
        end_h, end_m = map(int, end_str.split(":"))
        start_time = datetime.min.time().replace(hour=start_h, minute=start_m)
        end_time = datetime.min.time().replace(hour=end_h, minute=end_m)

        if start_time < end_time:
            return start_time <= now_time < end_time
        else:
            return now_time >= start_time or now_time < end_time
    except Exception as e:
        logger.error(f"Error checking quiet hours: {e}")
        return False


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
            
            # Setup hourly check for monthly goals reminders
            from apscheduler.triggers.cron import CronTrigger
            self.scheduler.add_job(
                check_and_send_monthly_goals_reminders,
                trigger=CronTrigger(minute=0, timezone=get_tz()),
                id="monthly_goals_check",
                replace_existing=True
            )
            logger.info("Monthly goals reminder check scheduled.")

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

    def schedule_recurring_task_job(self, task: Dict[str, Any]):
        """
        Schedules or updates an APScheduler job for a recurring task.
        """
        user_id = task["user_id"]
        task_id = task["id"]
        title = task["title"]
        repeat_type = task["repeat_type"]
        cron_expr = task.get("cron_expression")
        interval_days = task.get("interval_days")
        target_time = task.get("target_time") or "10:00"

        job_id = f"recurring_{user_id}_{task_id}"

        try:
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)

            tz = get_tz()
            hour, minute = map(int, target_time.split(":"))

            if repeat_type == "daily":
                from apscheduler.triggers.cron import CronTrigger
                trigger = CronTrigger(hour=hour, minute=minute, timezone=tz)
            elif repeat_type == "weekly":
                from apscheduler.triggers.cron import CronTrigger
                days_str = cron_expr or "mon,tue,wed,thu,fri,sat,sun"
                trigger = CronTrigger(day_of_week=days_str, hour=hour, minute=minute, timezone=tz)
            elif repeat_type == "interval_days" and interval_days:
                from apscheduler.triggers.interval import IntervalTrigger
                now = get_now()
                start_dt = datetime.combine(now.date(), datetime.min.time().replace(hour=hour, minute=minute), tzinfo=tz)
                if start_dt <= now:
                    start_dt += timedelta(days=1)
                trigger = IntervalTrigger(days=interval_days, start_date=start_dt, timezone=tz)
            elif repeat_type == "custom_cron" and cron_expr:
                from apscheduler.triggers.cron import CronTrigger
                trigger = CronTrigger.from_crontab(cron_expr, timezone=tz)
            else:
                from apscheduler.triggers.cron import CronTrigger
                trigger = CronTrigger(hour=hour, minute=minute, timezone=tz)

            self.scheduler.add_job(
                send_recurring_task_notification,
                trigger=trigger,
                id=job_id,
                args=[user_id, task_id, title],
                replace_existing=True
            )
            logger.info(f"Scheduled recurring task job {job_id} ('{title}') with trigger {trigger}")
        except Exception as e:
            logger.error(f"Failed to schedule recurring task job {job_id}: {e}", exc_info=True)

    def unschedule_recurring_task_job(self, task_id: int, user_id: int):
        """
        Removes a recurring task job from APScheduler.
        """
        job_id = f"recurring_{user_id}_{task_id}"
        try:
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)
                logger.info(f"Unscheduled recurring task job {job_id}")
        except Exception as e:
            logger.error(f"Failed to unschedule recurring task job {job_id}: {e}")

    async def load_all_recurring_tasks(self):
        """
        Loads all active recurring tasks from SQLite into APScheduler on startup.
        """
        from app.services.recurring import recurring_service
        try:
            tasks = await recurring_service.get_all_active_tasks()
            logger.info(f"Loading {len(tasks)} active recurring tasks into APScheduler...")
            for task in tasks:
                self.schedule_recurring_task_job(task)
        except Exception as e:
            logger.error(f"Failed to load recurring tasks on startup: {e}", exc_info=True)


async def send_periodic_schedule_summary(chat_id: int):
    """
    Callback function executed by APScheduler periodically to send current day schedule summary.
    """
    if is_in_quiet_hours():
        logger.info(f"Skipping periodic schedule summary for chat {chat_id} during quiet hours.")
        return

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


async def check_and_send_monthly_goals_reminders():
    """
    Checks all users with enabled goal reminders and dispatches monthly goals checklist if current time matches.
    """
    if is_in_quiet_hours():
        logger.info("Skipping monthly goals reminder check during quiet hours.")
        return
    from app.services.goals import goals_service
    from app.handlers.goals import format_month_name

    now = get_now()
    day_codes = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    current_day_code = day_codes[now.weekday()]
    current_time_str = now.strftime("%H:%M")
    current_month_str = now.strftime("%Y-%m")

    enabled_users = await goals_service.get_all_users_with_enabled_reminders()
    if not enabled_users:
        return

    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN)
    )
    try:
        for u in enabled_users:
            user_id = u["user_id"]
            days_str = u.get("days_of_week", "")
            time_str = u.get("time_str", "10:00")

            user_days = [d.strip() for d in days_str.split(",") if d.strip()]

            if current_day_code in user_days and current_time_str == time_str:
                goals = await goals_service.get_goals(user_id, current_month_str)
                if not goals:
                    continue

                completed_count = sum(1 for g in goals if g["is_completed"])
                total_count = len(goals)
                month_name = format_month_name(current_month_str)

                lines = [
                    f"🎯 **Еженедельный чек-лист целей на {month_name}:**",
                    f"📊 _Выполнено: {completed_count}/{total_count}_\n"
                ]

                for idx, g in enumerate(goals, 1):
                    icon = "✅" if g["is_completed"] else "⬜"
                    lines.append(f"{idx}. {icon} {g['goal_text']}")

                lines.append("\n💪 _Двигайся к своим целям шаг за шагом! Каждая точка — это твой результат._")

                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🎯 Открыть цели", callback_data=f"g_month:{current_month_str}")]
                ])

                msg_text = "\n".join(lines)
                try:
                    await bot.send_message(chat_id=user_id, text=msg_text, reply_markup=keyboard)
                except Exception as ex:
                    logger.error(f"Error sending goal reminder to user {user_id}: {ex}")
    except Exception as e:
        logger.error(f"Error in check_and_send_monthly_goals_reminders: {e}", exc_info=True)
    finally:
        await bot.session.close()


async def send_recurring_task_notification(user_id: int, task_id: int, title: str):
    """
    Callback executed when a recurring task / habit trigger fires.
    """
    if is_in_quiet_hours():
        logger.info(f"Skipping recurring task notification #{task_id} for user {user_id} during quiet hours.")
        return

    from app.services.recurring import recurring_service
    await recurring_service.update_last_triggered(task_id)

    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN)
    )
    try:
        msg_text = (
            f"🔁 **Повторяющаяся задача / Привычка:**\n\n"
            f"📌 **{title}**\n\n"
            f"Не забудь выполнить!"
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Сделано", callback_data=f"rec_done:{task_id}"),
                InlineKeyboardButton(text="⏳ Напомнить 15м", callback_data=f"rec_snooze:{task_id}"),
                InlineKeyboardButton(text="❌ Пропустить", callback_data=f"rec_skip:{task_id}")
            ]
        ])

        try:
            await bot.send_message(chat_id=user_id, text=msg_text, reply_markup=keyboard)
        except Exception:
            await bot.send_message(chat_id=user_id, text=f"🔁 Привычка:\n\n{title}", reply_markup=keyboard, parse_mode=None)
    except Exception as e:
        logger.error(f"Error sending recurring task notification for task #{task_id}: {e}", exc_info=True)
    finally:
        await bot.session.close()


scheduler_service = SchedulerService()
