import os
import tempfile
import unittest
from datetime import date
from app.config import settings
from app.agent.schemas import ToolExecutionContext
import app.agent.tools as tools
from app.services.tasks import tasks_service


class TestAgentTasks(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp_dir.name, "test_tasks.db")
        settings.DATABASE_PATH = self.db_path
        tasks_service.db_path = self.db_path
        tasks_service._init_db()

        self.context = ToolExecutionContext(user_id=12345, chat_id=12345)

    async def asyncTearDown(self):
        try:
            self.tmp_dir.cleanup()
        except Exception:
            pass

    async def test_create_and_get_task(self):
        res = await tools.create_task(self.context, text="Купить корм", target_date="2026-09-11")
        self.assertTrue(res.success)
        self.assertEqual(res.entity_type, "task")
        task_id = res.entity_id

        # Get tasks
        get_res = await tools.get_tasks(self.context, start_date="2026-09-11", end_date="2026-09-11")
        self.assertTrue(get_res.success)
        self.assertEqual(len(get_res.data), 1)
        self.assertEqual(get_res.data[0]["id"], task_id)
        self.assertEqual(get_res.data[0]["task_text"], "Купить корм")

    async def test_idempotent_task_creation(self):
        res1 = await tools.create_task(self.context, text="Проверить сервер", target_date="2026-09-11")
        res2 = await tools.create_task(self.context, text="Проверить сервер", target_date="2026-09-11")
        self.assertTrue(res1.success)
        self.assertTrue(res2.success)
        self.assertEqual(res1.entity_id, res2.entity_id)

    async def test_search_and_complete_task(self):
        res = await tools.create_task(self.context, text="Стоматолог осмотр", target_date="2026-09-11")
        task_id = res.entity_id

        # Search
        s_res = await tools.search_tasks(self.context, query="стоматолог")
        self.assertTrue(s_res.success)
        self.assertEqual(len(s_res.data), 1)
        self.assertEqual(s_res.data[0]["id"], task_id)

        # Complete
        c_res = await tools.complete_task(self.context, task_id=task_id)
        self.assertTrue(c_res.success)

    async def test_move_and_delete_task(self):
        res = await tools.create_task(self.context, text="Позвонить Саше", target_date="2026-09-11")
        task_id = res.entity_id

        # Move
        m_res = await tools.move_task(self.context, task_id=task_id, target_date="2026-09-15", new_time="14:00")
        self.assertTrue(m_res.success)
        self.assertEqual(m_res.data["new_date"], "2026-09-15")
        self.assertIn("14:00", m_res.data["task_text"])

        # Delete
        d_res = await tools.delete_task(self.context, task_id=task_id)
        self.assertTrue(d_res.success)

    async def test_clear_tasks_confirmation(self):
        await tools.create_task(self.context, text="Task 1", target_date="2026-09-11")
        await tools.create_task(self.context, text="Task 2", target_date="2026-09-11")

        # Without confirmation -> must require confirmation
        unconfirmed = await tools.clear_tasks(self.context, target_date="2026-09-11", confirmed=False)
        self.assertFalse(unconfirmed.success)
        self.assertEqual(unconfirmed.error_code, "confirmation_required")

        # With confirmation -> succeeds
        confirmed = await tools.clear_tasks(self.context, target_date="2026-09-11", confirmed=True)
        self.assertTrue(confirmed.success)
        self.assertEqual(confirmed.data["deleted_count"], 2)


if __name__ == "__main__":
    unittest.main()
