import unittest
from unittest.mock import AsyncMock, patch, MagicMock
from app.agent.schemas import ToolExecutionContext
import app.agent.tools as tools
from app.services.calendar import calendar_service


class TestAgentCalendar(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.context = ToolExecutionContext(user_id=123, chat_id=123)

    async def test_unconfigured_calendar_error(self):
        # When service is unconfigured
        with patch.object(calendar_service, "is_configured", return_value=False):
            res = await tools.create_calendar_event(
                self.context,
                title="Стоматолог",
                start_time="2026-09-11T15:00:00"
            )
            self.assertFalse(res.success)
            self.assertEqual(res.error_code, "calendar_not_configured")
            self.assertIn("не настроена", res.message)

    async def test_configured_calendar_event_creation(self):
        mock_event = {"id": "cal_123", "summary": "Стоматолог", "htmlLink": "https://calendar.google.com/..."}
        with patch.object(calendar_service, "is_configured", return_value=True), \
             patch.object(calendar_service, "create_event", new=AsyncMock(return_value=mock_event)):

            res = await tools.create_calendar_event(
                self.context,
                title="Стоматолог",
                start_time="2026-09-11T15:00:00"
            )
            self.assertTrue(res.success)
            self.assertEqual(res.entity_id, "cal_123")
            self.assertEqual(res.data["summary"], "Стоматолог")


if __name__ == "__main__":
    unittest.main()
