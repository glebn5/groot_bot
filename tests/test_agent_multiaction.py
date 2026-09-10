import os
import tempfile
import unittest
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, patch

from app.agent.agent import GrootAgent
from app.agent.providers import LLMProvider, LLMTurnResponse, ToolCallRequest
from app.agent.schemas import ToolExecutionContext
from app.agent.registry import build_default_registry
from app.config import settings
from app.services.tasks import tasks_service
from app.services.scheduler import scheduler_service
from app.services.calendar import calendar_service
from app.services.context import context_service
from app.utils.timezone import get_now, get_today


class MockLLMProvider(LLMProvider):
    def __init__(self, responses=None):
        self.responses = responses or []
        self.call_history = []

    def queue_turn(self, turn_response: LLMTurnResponse):
        self.responses.append(turn_response)

    async def generate_turn(self, messages, tools=None):
        self.call_history.append(messages)
        if self.responses:
            return self.responses.pop(0)
        return LLMTurnResponse(content="🌴 Готово!", tool_calls=[])


class TestAgentMultiAction(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp_dir.name, "test_agent.db")
        settings.DATABASE_PATH = self.db_path
        tasks_service.db_path = self.db_path
        tasks_service._init_db()

        if not scheduler_service.scheduler.running:
            scheduler_service.start()

        self.user_id = 55555
        self.chat_id = 55555
        context_service.clear_context(self.user_id)
        self._cleanup_reminders()

        self.mock_provider = MockLLMProvider()
        self.registry = build_default_registry()
        self.agent = GrootAgent(registry=self.registry, provider=self.mock_provider)

    def _cleanup_reminders(self):
        try:
            for job in list(scheduler_service.scheduler.get_jobs()):
                if job.args and len(job.args) > 0 and job.args[0] == self.chat_id:
                    try:
                        job.remove()
                    except Exception:
                        pass
        except Exception:
            pass

    async def asyncTearDown(self):
        self._cleanup_reminders()
        try:
            self.tmp_dir.cleanup()
        except Exception:
            pass
        context_service.clear_context(self.user_id)

    async def test_acceptance_1_single_reminder(self):
        """
        Input: 'Напомни завтра в 15:00 позвонить Саше'
        Expected: tool create_reminder executed, confirmation returned.
        """
        now = get_now()
        tomorrow_15 = (now + timedelta(days=1)).strftime("%Y-%m-%d 15:00:00")

        # LLM emits create_reminder tool call
        self.mock_provider.queue_turn(LLMTurnResponse(
            tool_calls=[
                ToolCallRequest(
                    id="call_1",
                    name="create_reminder",
                    arguments={"message": "Позвонить Саше", "trigger_at": tomorrow_15}
                )
            ]
        ))
        # After tool returns, LLM generates natural final response
        self.mock_provider.queue_turn(LLMTurnResponse(
            content="🌴 Готово! Напомню завтра в 15:00 позвонить Саше."
        ))

        res = await self.agent.process_message(self.user_id, self.chat_id, "Напомни завтра в 15:00 позвонить Саше")
        self.assertEqual(res.tool_calls_count, 1)
        self.assertIn("Саше", res.reply_text)

    async def test_acceptance_2_clarify_missing_time(self):
        """
        Input: 'Напомни завтра позвонить Саше'
        Expected: Agent does not invent time, asks clarifying question: 'Во сколько?'
        """
        self.mock_provider.queue_turn(LLMTurnResponse(
            content="🌴 Во сколько завтра напомнить позвонить Саше?"
        ))

        res = await self.agent.process_message(self.user_id, self.chat_id, "Напомни завтра позвонить Саше")
        self.assertEqual(res.tool_calls_count, 0)
        self.assertIn("Во сколько", res.reply_text)

    async def test_acceptance_3_context_followup(self):
        """
        Turn 1: 'Напомни завтра позвонить Саше' -> Bot asks 'Во сколько?'
        Turn 2: 'В 15' -> Agent uses previous context to schedule reminder
        """
        # Turn 1
        self.mock_provider.queue_turn(LLMTurnResponse(
            content="🌴 Во сколько завтра напомнить позвонить Саше?"
        ))
        await self.agent.process_message(self.user_id, self.chat_id, "Напомни завтра позвонить Саше")

        # Turn 2: User says "В 15"
        now = get_now()
        tomorrow_15 = (now + timedelta(days=1)).strftime("%Y-%m-%d 15:00:00")

        self.mock_provider.queue_turn(LLMTurnResponse(
            tool_calls=[
                ToolCallRequest(
                    id="call_followup",
                    name="create_reminder",
                    arguments={"message": "Позвонить Саше", "trigger_at": tomorrow_15}
                )
            ]
        ))
        self.mock_provider.queue_turn(LLMTurnResponse(
            content="🌴 Договорились! Напомню завтра в 15:00 позвонить Саше."
        ))

        res2 = await self.agent.process_message(self.user_id, self.chat_id, "В 15")
        self.assertEqual(res2.tool_calls_count, 1)
        self.assertIn("15:00", res2.reply_text)

    async def test_acceptance_4_combined_calendar_and_reminder(self):
        """
        Input: 'Завтра стоматолог в 15:00, добавь в календарь и напомни за час'
        Expected: 2 tool calls: create_calendar_event at 15:00 AND create_reminder at 14:00
        """
        now = get_now()
        tomorrow = now + timedelta(days=1)
        event_start = tomorrow.strftime("%Y-%m-%dT15:00:00")
        reminder_at = tomorrow.strftime("%Y-%m-%d 14:00:00")

        mock_event = {"id": "ev_1", "summary": "Стоматолог", "htmlLink": "https://..."}

        with patch.object(calendar_service, "is_configured", return_value=True), \
             patch.object(calendar_service, "create_event", new=AsyncMock(return_value=mock_event)):

            self.mock_provider.queue_turn(LLMTurnResponse(
                tool_calls=[
                    ToolCallRequest(
                        id="c1",
                        name="create_calendar_event",
                        arguments={"title": "Стоматолог", "start_time": event_start}
                    ),
                    ToolCallRequest(
                        id="c2",
                        name="create_reminder",
                        arguments={"message": "Стоматолог через час", "trigger_at": reminder_at}
                    )
                ]
            ))
            self.mock_provider.queue_turn(LLMTurnResponse(
                content="🌴 Готово! Событие «Стоматолог» добавлено в календарь на 15:00, и напоминание установлено на 14:00."
            ))

            res = await self.agent.process_message(
                self.user_id,
                self.chat_id,
                "Завтра стоматолог в 15:00, добавь в календарь и напомни за час"
            )
            self.assertEqual(res.tool_calls_count, 2)
            self.assertIn("Стоматолог", res.reply_text)

    async def test_acceptance_5_get_schedule(self):
        """
        Input: 'Что у меня завтра?'
        Expected: get_schedule called with tomorrow's date
        """
        tomorrow_str = (get_today() + timedelta(days=1)).strftime("%Y-%m-%d")

        self.mock_provider.queue_turn(LLMTurnResponse(
            tool_calls=[
                ToolCallRequest(
                    id="sched_1",
                    name="get_schedule",
                    arguments={"start_date": tomorrow_str}
                )
            ]
        ))
        self.mock_provider.queue_turn(LLMTurnResponse(
            content="🌴 На завтра у вас запланировано: 1 задача и 1 напоминание."
        ))

        res = await self.agent.process_message(self.user_id, self.chat_id, "Что у меня завтра?")
        self.assertEqual(res.tool_calls_count, 1)
        self.assertEqual(res.executed_tools[0]["name"], "get_schedule")

    async def test_contextual_date_inheritance_after_schedule_query(self):
        """
        Turn 1: User: 'что на завтра' -> Agent shows schedule for tomorrow
        Turn 2: User: 'закинь еще: купить булки' -> Task saved on tomorrow, NOT today
        """
        tomorrow = get_today() + timedelta(days=1)
        tomorrow_str = tomorrow.strftime("%Y-%m-%d")

        # Turn 1
        self.mock_provider.queue_turn(LLMTurnResponse(
            tool_calls=[
                ToolCallRequest(
                    id="sched_1",
                    name="get_schedule",
                    arguments={"start_date": tomorrow_str}
                )
            ]
        ))
        self.mock_provider.queue_turn(LLMTurnResponse(
            content=f"✨ План на завтра, {tomorrow_str}: 1 задача."
        ))
        await self.agent.process_message(self.user_id, self.chat_id, "что на завтра")

        # Verify context date was set to tomorrow
        self.assertEqual(context_service.get_last_date(self.user_id), tomorrow)

        # Turn 2: User says "закинь еще: купить булки"
        self.mock_provider.queue_turn(LLMTurnResponse(
            tool_calls=[
                ToolCallRequest(
                    id="task_followup",
                    name="create_task",
                    arguments={"text": "купить булки"}
                )
            ]
        ))
        self.mock_provider.queue_turn(LLMTurnResponse(
            content=f"🌴 Задача «купить булки» добавлена на завтра, {tomorrow_str}!"
        ))

        res2 = await self.agent.process_message(self.user_id, self.chat_id, "закинь еще: купить булки")
        self.assertEqual(res2.tool_calls_count, 1)

        # Check that task in DB was created for tomorrow, NOT today
        tasks_tomorrow = await tasks_service.get_tasks(self.user_id, tomorrow)
        self.assertEqual(len(tasks_tomorrow), 1)
        self.assertEqual(tasks_tomorrow[0]["task_text"], "купить булки")

        # Ensure no tasks were added to today
        tasks_today = await tasks_service.get_tasks(self.user_id, get_today())
        self.assertEqual(len(tasks_today), 0)

    async def test_add_task_with_date_and_prefix_cleaned_no_reminder(self):
        """
        Input: 'добавь на завтра: пофиксить бота'
        Expected:
        - task_text cleaned of prefix -> 'пофиксить бота'
        - target_date = tomorrow
        - NO reminder created
        """
        tomorrow = get_today() + timedelta(days=1)
        tomorrow_str = tomorrow.strftime("%Y-%m-%d")

        self.mock_provider.queue_turn(LLMTurnResponse(
            tool_calls=[
                ToolCallRequest(
                    id="fix_bot",
                    name="create_task",
                    arguments={"text": "добавь на завтра: пофиксить бота", "target_date": tomorrow_str}
                )
            ]
        ))
        self.mock_provider.queue_turn(LLMTurnResponse(
            content="🌴 Добавил задачу «пофиксить бота» на завтра!"
        ))

        res = await self.agent.process_message(self.user_id, self.chat_id, "добавь на завтра: пофиксить бота")
        self.assertEqual(res.tool_calls_count, 1)
        self.assertEqual(res.executed_tools[0]["name"], "create_task")

        tasks_tomorrow = await tasks_service.get_tasks(self.user_id, tomorrow)
        self.assertEqual(len(tasks_tomorrow), 1)
        self.assertEqual(tasks_tomorrow[0]["task_text"], "пофиксить бота")

        # Verify NO reminders were scheduled
        rems = scheduler_service.get_reminders_for_date(tomorrow, chat_id=self.chat_id)
        self.assertEqual(len(rems), 0)

    async def test_contextual_date_inheritance_today(self):
        """
        Turn 1: User: 'что на сегодня' -> Agent shows schedule for today
        Turn 2: User: 'добавь ещё позвонить Ивану' -> Task saved on today with cleaned text
        """
        today = get_today()
        today_str = today.strftime("%Y-%m-%d")

        self.mock_provider.queue_turn(LLMTurnResponse(
            tool_calls=[
                ToolCallRequest(
                    id="sched_today",
                    name="get_schedule",
                    arguments={"start_date": today_str}
                )
            ]
        ))
        self.mock_provider.queue_turn(LLMTurnResponse(
            content=f"✨ План на сегодня, {today_str}."
        ))
        await self.agent.process_message(self.user_id, self.chat_id, "что на сегодня")

        self.mock_provider.queue_turn(LLMTurnResponse(
            tool_calls=[
                ToolCallRequest(
                    id="add_ivan",
                    name="create_task",
                    arguments={"text": "добавь ещё позвонить Ивану"}
                )
            ]
        ))
        self.mock_provider.queue_turn(LLMTurnResponse(
            content="🌴 Задача «позвонить Ивану» добавлена на сегодня!"
        ))

        res2 = await self.agent.process_message(self.user_id, self.chat_id, "добавь ещё позвонить Ивану")
        self.assertEqual(res2.tool_calls_count, 1)

        tasks_today = await tasks_service.get_tasks(self.user_id, today)
        self.assertEqual(len(tasks_today), 1)
        self.assertEqual(tasks_today[0]["task_text"], "позвонить Ивану")

    async def test_move_last_discussed_task_context_reference(self):
        """
        Turn 1: Task created in context
        Turn 2: User: 'перенеси его на пятницу' -> Agent uses last discussed task ID from context
        """
        res1 = await self.registry.execute("create_task", {"text": "пофиксить бота", "target_date": "2026-09-11"}, ToolExecutionContext(user_id=self.user_id, chat_id=self.chat_id))
        task_id = res1.entity_id

        self.mock_provider.queue_turn(LLMTurnResponse(
            tool_calls=[
                ToolCallRequest(
                    id="move_it",
                    name="move_task",
                    arguments={"target_date": "2026-09-12"}
                )
            ]
        ))
        self.mock_provider.queue_turn(LLMTurnResponse(
            content="🌴 Перенёс задачу «пофиксить бота» на 12.09.2026!"
        ))

        res2 = await self.agent.process_message(self.user_id, self.chat_id, "перенеси его на пятницу")
        self.assertEqual(res2.tool_calls_count, 1)

        updated_task = await tasks_service.get_task_by_id(self.user_id, task_id)
        self.assertEqual(updated_task["target_date"], "2026-09-12")

    async def test_acceptance_6_search_everything(self):
        """
        Input: 'Найди когда у меня стоматолог'
        Expected: search_everything called with 'стоматолог'
        """
        self.mock_provider.queue_turn(LLMTurnResponse(
            tool_calls=[
                ToolCallRequest(
                    id="search_1",
                    name="search_everything",
                    arguments={"query": "стоматолог"}
                )
            ]
        ))
        self.mock_provider.queue_turn(LLMTurnResponse(
            content="🌴 Нашёл: событие «Стоматолог» в календаре на 15:00 завтра."
        ))

        res = await self.agent.process_message(self.user_id, self.chat_id, "Найди когда у меня стоматолог")
        self.assertEqual(res.tool_calls_count, 1)
        self.assertEqual(res.executed_tools[0]["name"], "search_everything")

    async def test_acceptance_7_search_then_move(self):
        """
        Input: 'Перенеси стоматолога на пятницу'
        Expected: Search first, then move task.
        """
        # Create a task first in DB
        task_id = await tasks_service.add_task(self.user_id, "Стоматолог", target_date=get_today())

        # Step 1: Agent calls search_tasks
        self.mock_provider.queue_turn(LLMTurnResponse(
            tool_calls=[
                ToolCallRequest(
                    id="step1",
                    name="search_tasks",
                    arguments={"query": "стоматолог"}
                )
            ]
        ))
        # Step 2: Agent sees the task #{task_id}, calls move_task to Friday
        friday_str = (get_today() + timedelta(days=1)).strftime("%Y-%m-%d")
        self.mock_provider.queue_turn(LLMTurnResponse(
            tool_calls=[
                ToolCallRequest(
                    id="step2",
                    name="move_task",
                    arguments={"task_id": task_id, "target_date": friday_str}
                )
            ]
        ))
        # Step 3: Final response
        self.mock_provider.queue_turn(LLMTurnResponse(
            content="🌴 Перенёс приём у стоматолога на пятницу!"
        ))

        res = await self.agent.process_message(self.user_id, self.chat_id, "Перенеси стоматолога на пятницу")
        self.assertEqual(res.tool_calls_count, 2)
        self.assertEqual(res.executed_tools[0]["name"], "search_tasks")
        self.assertEqual(res.executed_tools[1]["name"], "move_task")
        self.assertIn("пятницу", res.reply_text)

    async def test_acceptance_8_destructive_clear_confirmation(self):
        """
        Input: 'Удали все задачи'
        Expected: clear_tasks returns confirmation_required, agent asks user to confirm.
        Follow-up: 'Да, удали' -> confirms and executes.
        """
        self.mock_provider.queue_turn(LLMTurnResponse(
            tool_calls=[
                ToolCallRequest(
                    id="clear_1",
                    name="clear_tasks",
                    arguments={"target_date": None, "confirmed": False}
                )
            ]
        ))

        res = await self.agent.process_message(self.user_id, self.chat_id, "Удали все задачи")
        self.assertTrue(res.requires_confirmation)
        self.assertIn("подтверждение", res.reply_text.lower())

        # Follow-up: User confirms
        res_conf = await self.agent.process_message(self.user_id, self.chat_id, "Да, подтверждаю")
        self.assertIn("Удалено", res_conf.reply_text)

    async def test_acceptance_9_save_note(self):
        """
        Input: 'Запомни, что пароль от тестового сервера хранится в Bitwarden'
        Expected: create_note called, not create_task
        """
        self.mock_provider.queue_turn(LLMTurnResponse(
            tool_calls=[
                ToolCallRequest(
                    id="note_1",
                    name="create_note",
                    arguments={"content": "Пароль от тестового сервера хранится в Bitwarden"}
                )
            ]
        ))
        self.mock_provider.queue_turn(LLMTurnResponse(
            content="🌴 Запомнил! Заметка сохранена."
        ))

        res = await self.agent.process_message(
            self.user_id,
            self.chat_id,
            "Запомни, что пароль от тестового сервера хранится в Bitwarden"
        )
        self.assertEqual(res.tool_calls_count, 1)
        self.assertEqual(res.executed_tools[0]["name"], "create_note")

    async def test_acceptance_10_recurring_habit(self):
        """
        Input: 'Каждый день в 9 утра напоминай пить воду'
        Expected: create_recurring_task called with repeat_type='daily' and time_of_day='09:00'
        """
        self.mock_provider.queue_turn(LLMTurnResponse(
            tool_calls=[
                ToolCallRequest(
                    id="rec_1",
                    name="create_recurring_task",
                    arguments={"title": "Пить воду", "repeat_type": "daily", "time_of_day": "09:00"}
                )
            ]
        ))
        self.mock_provider.queue_turn(LLMTurnResponse(
            content="🌴 Отлично! Создал ежедневную привычку «Пить воду» на 09:00."
        ))

        res = await self.agent.process_message(
            self.user_id,
            self.chat_id,
            "Каждый день в 9 утра напоминай пить воду"
        )
        self.assertEqual(res.tool_calls_count, 1)
        self.assertEqual(res.executed_tools[0]["name"], "create_recurring_task")

    async def test_acceptance_11_multitask_batch(self):
        """
        Input: Multitask message with 3 tasks and 1 reminder
        Expected: 3 create_task calls + 1 create_reminder call (4 tool calls total)
        """
        now = get_now()
        rem_time = (now + timedelta(hours=3)).strftime("%Y-%m-%d 18:00:00")
        today_str = get_today().strftime("%Y-%m-%d")

        self.mock_provider.queue_turn(LLMTurnResponse(
            tool_calls=[
                ToolCallRequest(id="t1", name="create_task", arguments={"text": "Купить корм", "target_date": today_str}),
                ToolCallRequest(id="t2", name="create_task", arguments={"text": "Проверить сервер", "target_date": today_str}),
                ToolCallRequest(id="t3", name="create_task", arguments={"text": "Написать Саше", "target_date": today_str}),
                ToolCallRequest(id="r1", name="create_reminder", arguments={"message": "Проверить сервер", "trigger_at": rem_time}),
            ]
        ))
        self.mock_provider.queue_turn(LLMTurnResponse(
            content="🌴 Всё записал: 3 задачи на сегодня и напоминание про сервер на 18:00!"
        ))

        input_text = "Сегодня:\n- купить корм\n- проверить сервер\n- написать Саше\nНапомни в 18:00 про сервер"
        res = await self.agent.process_message(self.user_id, self.chat_id, input_text)
        self.assertEqual(res.tool_calls_count, 4)


if __name__ == "__main__":
    unittest.main()
