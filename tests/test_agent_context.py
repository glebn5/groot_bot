import unittest
from datetime import date
from app.services.context import context_service


class TestAgentContext(unittest.TestCase):
    def setUp(self):
        self.user_id = 9999
        context_service.clear_context(self.user_id)

    def tearDown(self):
        context_service.clear_context(self.user_id)

    def test_message_history(self):
        context_service.add_message(self.user_id, "user", "Привет, Грут!")
        context_service.add_message(self.user_id, "assistant", "🌴 Привет!")

        msgs = context_service.get_messages(self.user_id)
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[0]["content"], "Привет, Грут!")
        self.assertEqual(msgs[1]["content"], "🌴 Привет!")

    def test_last_referenced_entities(self):
        context_service.set_last_entity(self.user_id, "task", {
            "id": 42,
            "text": "Позвонить Саше",
            "date": "2026-09-11"
        })
        context_service.set_last_entity(self.user_id, "reminder", {
            "id": "job_99",
            "message": "Позвонить Саше",
            "time": "15:00"
        })
        context_service.set_last_date(self.user_id, date(2026, 9, 11))

        t = context_service.get_last_entity(self.user_id, "task")
        self.assertIsNotNone(t)
        self.assertEqual(t["id"], 42)

        r = context_service.get_last_entity(self.user_id, "reminder")
        self.assertIsNotNone(r)
        self.assertEqual(r["time"], "15:00")

        summary = context_service.get_context_summary(self.user_id)
        self.assertIn("Позвонить Саше", summary)
        self.assertIn("2026-09-11", summary)

    def test_pending_confirmation(self):
        context_service.set_pending_confirmation(self.user_id, {"action": "clear_tasks", "target_date": None})
        pending = context_service.get_pending_confirmation(self.user_id)
        self.assertIsNotNone(pending)
        self.assertEqual(pending["action"], "clear_tasks")

        context_service.clear_pending_confirmation(self.user_id)
        self.assertIsNone(context_service.get_pending_confirmation(self.user_id))


if __name__ == "__main__":
    unittest.main()
