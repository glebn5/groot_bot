import unittest
import asyncio
from datetime import datetime, date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from app.agent.ai_manager import AIRequestManager
from app.services.calendar import CalendarService
from app.agent.schemas import ToolExecutionContext
from app.agent.tools import create_calendar_event


class TestReliabilityAndLimits(unittest.TestCase):

    def setUp(self):
        self.ai_manager = AIRequestManager()

    def test_model_lists_validity(self):
        # 1. Non-existent llama-3.3-70b-versatile must NOT be in any list
        self.assertNotIn("llama-3.3-70b-versatile", self.ai_manager.GROQ_TOOL_MODELS)
        self.assertNotIn("llama-3.3-70b-versatile", self.ai_manager.GROQ_TEXT_MODELS)

        # 2. groq/compound must NOT be in GROQ_TOOL_MODELS because it rejects tool calling
        self.assertNotIn("groq/compound", self.ai_manager.GROQ_TOOL_MODELS)
        self.assertNotIn("groq/compound-mini", self.ai_manager.GROQ_TOOL_MODELS)

        # 3. Valid tool calling models must be present (including llama-3.1-8b-instant)
        self.assertIn("openai/gpt-oss-120b", self.ai_manager.GROQ_TOOL_MODELS)
        self.assertIn("openai/gpt-oss-20b", self.ai_manager.GROQ_TOOL_MODELS)
        self.assertIn("llama-3.1-8b-instant", self.ai_manager.GROQ_TOOL_MODELS)

    def test_rate_limit_parsing(self):
        # Case A: minutes and seconds
        err1 = Exception("Rate limit reached. Limit 200000, Used 196929. Please try again in 11m2.688s.")
        cooldown1 = self.ai_manager.parse_rate_limit_cooldown(err1)
        self.assertGreaterEqual(cooldown1, 660.0)

        # Case B: seconds only
        err2 = Exception("Rate limit reached. Please try again in 4.5s.")
        cooldown2 = self.ai_manager.parse_rate_limit_cooldown(err2)
        self.assertGreaterEqual(cooldown2, 6.0)

        # Case C: TPD tokens per day mention
        err3 = Exception("TPD limit reached for model.")
        cooldown3 = self.ai_manager.parse_rate_limit_cooldown(err3)
        self.assertGreaterEqual(cooldown3, 600.0)

    def test_model_cooldown_behavior(self):
        model = "openai/gpt-oss-120b"
        cooling, rem = self.ai_manager.is_model_on_cooldown(model)
        self.assertFalse(cooling)

        # Put on cooldown
        self.ai_manager.set_model_cooldown(model, 10.0, reason="Test 429")
        cooling, rem = self.ai_manager.is_model_on_cooldown(model)
        self.assertTrue(cooling)
        self.assertGreater(rem, 0.0)

    def test_calendar_duplicate_prevention(self):
        cal = CalendarService()
        cal.service = MagicMock()

        # Mock existing events for today
        target_date = date(2026, 9, 11)
        start_time = datetime(2026, 9, 11, 14, 0)
        existing_event = {
            "id": "ev_gorshkov_birthday_123",
            "summary": "День рождения Горшкова",
            "start": {"dateTime": "2026-09-11T14:00:00"}
        }

        async def run_async():
            with patch.object(cal, "get_events_for_date", new=AsyncMock(return_value=[existing_event])):
                # Check duplicate detection
                dup = await cal.find_duplicate_event("День рождения Горшкова", start_time)
                self.assertIsNotNone(dup)
                self.assertEqual(dup["id"], "ev_gorshkov_birthday_123")

                # Test create_event returns duplicate without insert
                res = await cal.create_event("День рождения Горшкова", start_time)
                self.assertEqual(res["id"], "ev_gorshkov_birthday_123")
                cal.service.events().insert.assert_not_called()

        asyncio.run(run_async())

    def test_prompt_caching_structure(self):
        from app.agent.prompts import GROOT_CORE_PROMPT, GROOT_RUNTIME_CONTEXT_TEMPLATE
        # Core prompt must NOT contain dynamic timestamps or formatting variables
        self.assertNotIn("{current_datetime}", GROOT_CORE_PROMPT)
        self.assertNotIn("{timezone}", GROOT_CORE_PROMPT)
        self.assertNotIn("{context_summary}", GROOT_CORE_PROMPT)

        # Dynamic context template must contain them
        self.assertIn("{current_datetime}", GROOT_RUNTIME_CONTEXT_TEMPLATE)
        self.assertIn("{timezone}", GROOT_RUNTIME_CONTEXT_TEMPLATE)

    def test_update_goal_execution(self):
        from app.services.goals import goals_service
        from app.agent.tools import update_goal

        async def run_async():
            # Add a test goal
            user_id = 999888777
            gid = await goals_service.add_goal(user_id, "3000 подтягиваний", "2026-09")
            self.assertGreater(gid, 0)

            # Update via tool
            ctx = ToolExecutionContext(user_id=user_id, chat_id=user_id, message_id=1)
            res = await update_goal(ctx, gid, "172 из 3000 подтягиваний")
            self.assertTrue(res.success)
            self.assertIn("172 из 3000 подтягиваний", res.message)

            # Verify in DB
            goals = await goals_service.get_goals(user_id, "2026-09")
            updated = next((g for g in goals if g["id"] == gid), None)
            self.assertIsNotNone(updated)
            self.assertEqual(updated["goal_text"], "172 из 3000 подтягиваний")

            # Clean up
            await goals_service.delete_goal(gid, user_id)

        asyncio.run(run_async())

    def test_cache_metrics_logging(self):
        # Mock response with usage and cached_tokens
        mock_resp = MagicMock()
        mock_resp.usage.prompt_tokens = 6000
        mock_resp.usage.completion_tokens = 120
        mock_resp.usage.prompt_tokens_details.cached_tokens = 5400

        with patch("app.agent.ai_manager.logger.info") as mock_log:
            self.ai_manager._log_cache_metrics(mock_resp, provider_name="ProxyAPI")
            mock_log.assert_called_once()
            log_msg = mock_log.call_args[0][0]
            self.assertIn("[ProxyAPI Cache]", log_msg)
            self.assertIn("6000 tokens", log_msg)
            self.assertIn("Cached: 5400", log_msg)
            self.assertIn("Hit-rate: 90.0%", log_msg)


if __name__ == "__main__":
    unittest.main()

