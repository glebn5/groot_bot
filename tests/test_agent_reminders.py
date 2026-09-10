import unittest
from datetime import datetime, timedelta
from app.agent.schemas import ToolExecutionContext
import app.agent.tools as tools
from app.services.scheduler import scheduler_service
from app.utils.timezone import get_now


class TestAgentReminders(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        if not scheduler_service.scheduler.running:
            scheduler_service.start()
        self.context = ToolExecutionContext(user_id=777, chat_id=777)
        self.created_jobs = []

    async def asyncTearDown(self):
        for j_id in self.created_jobs:
            try:
                scheduler_service.remove_reminder(j_id)
            except Exception:
                pass

    async def test_create_and_get_reminder(self):
        now = get_now()
        target_dt = now + timedelta(hours=2)
        target_str = target_dt.strftime("%Y-%m-%d %H:%M:%S")

        res = await tools.create_reminder(self.context, message="Позвонить Саше", trigger_at=target_str)
        self.assertTrue(res.success)
        self.assertEqual(res.entity_type, "reminder")
        job_id = res.entity_id
        self.created_jobs.append(job_id)

        # Get reminders
        g_res = await tools.get_reminders(self.context, start_date=target_dt.strftime("%Y-%m-%d"))
        self.assertTrue(g_res.success)
        found = any(r["id"] == str(job_id) for r in g_res.data)
        self.assertTrue(found)

    async def test_reject_past_reminder(self):
        now = get_now()
        past_dt = now - timedelta(hours=2)
        res = await tools.create_reminder(self.context, message="Вчерашнее дело", trigger_at=past_dt.strftime("%Y-%m-%d %H:%M:%S"))
        self.assertFalse(res.success)
        self.assertEqual(res.error_code, "past_datetime")

    async def test_idempotent_reminder_creation(self):
        now = get_now()
        target_dt = now + timedelta(hours=3)
        target_str = target_dt.strftime("%Y-%m-%d %H:%M:%S")

        res1 = await tools.create_reminder(self.context, message="Оплатить интернет", trigger_at=target_str)
        self.assertTrue(res1.success)
        self.created_jobs.append(res1.entity_id)

        res2 = await tools.create_reminder(self.context, message="Оплатить интернет", trigger_at=target_str)
        self.assertTrue(res2.success)
        self.assertEqual(res1.entity_id, res2.entity_id)

    async def test_update_and_delete_reminder(self):
        now = get_now()
        target_dt = now + timedelta(hours=4)
        res = await tools.create_reminder(self.context, message="Совещание", trigger_at=target_dt.strftime("%Y-%m-%d %H:%M:%S"))
        job_id = str(res.entity_id)
        self.created_jobs.append(job_id)

        # Update
        new_dt = target_dt + timedelta(hours=1)
        up_res = await tools.update_reminder(self.context, reminder_id=job_id, new_trigger_at=new_dt.strftime("%Y-%m-%d %H:%M:%S"), new_message="Срочное совещание")
        self.assertTrue(up_res.success)

        # Delete
        del_res = await tools.delete_reminder(self.context, reminder_id=job_id)
        self.assertTrue(del_res.success)


if __name__ == "__main__":
    unittest.main()
