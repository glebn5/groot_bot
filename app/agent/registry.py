import logging
import inspect
from typing import Dict, Any, Callable, List, Optional
from app.agent.schemas import ToolExecutionContext, ToolResult
import app.agent.tools as t

logger = logging.getLogger(__name__)


class ToolRegistry:
    """
    Typed, secure registry of tools accessible to the AI Agent.
    Strictly forbids eval/exec and guarantees trusted user context injection.
    """
    def __init__(self):
        self._tools: Dict[str, Dict[str, Any]] = {}

    def register(
        self,
        name: str,
        description: str,
        parameters: Dict[str, Any],
        handler: Callable
    ):
        self._tools[name] = {
            "name": name,
            "description": description.strip(),
            "parameters": parameters,
            "handler": handler
        }

    def get_tool(self, name: str) -> Optional[Dict[str, Any]]:
        return self._tools.get(name)

    def get_openai_tools(self) -> List[Dict[str, Any]]:
        """
        Converts registered tools to OpenAI / Groq tool specification format.
        """
        tools_spec = []
        for name, spec in self._tools.items():
            tools_spec.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": spec["description"],
                    "parameters": spec["parameters"]
                }
            })
        return tools_spec

    async def execute(
        self,
        name: str,
        arguments: Dict[str, Any],
        context: ToolExecutionContext
    ) -> ToolResult:
        """
        Safely executes a registered tool, injecting the trusted context.
        """
        tool_spec = self._tools.get(name)
        if not tool_spec:
            logger.warning(f"Attempted to call unregistered tool: '{name}'")
            return ToolResult(
                success=False,
                error_code="tool_not_found",
                message=f"Инструмент «{name}» не существует."
            )

        handler = tool_spec["handler"]
        try:
            logger.info(f"Executing tool '{name}' for user_id={context.user_id} with args={arguments}")
            # Ensure context parameter is injected safely
            sig = inspect.signature(handler)
            call_kwargs = dict(arguments)
            if "context" in sig.parameters:
                call_kwargs["context"] = context

            if inspect.iscoroutinefunction(handler):
                result = await handler(**call_kwargs)
            else:
                result = handler(**call_kwargs)

            if isinstance(result, ToolResult):
                return result
            return ToolResult(success=True, data=result)
        except Exception as e:
            logger.error(f"Error executing tool '{name}': {e}", exc_info=True)
            return ToolResult(
                success=False,
                error_code="tool_execution_error",
                message=f"Ошибка выполнения инструмента «{name}»: {str(e)}"
            )


def build_default_registry() -> ToolRegistry:
    registry = ToolRegistry()

    # 1. Tasks
    registry.register(
        name="create_task",
        description=(
            "Создаёт новую задачу на указанную дату (или на сегодня). "
            "Используй для добавления дел в список задач на день. "
            "НЕ используй для установки напоминаний с точным временем (для этого используй create_reminder)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Текст задачи (например: 'Купить молоко')"},
                "target_date": {"type": "string", "description": "Целевая дата (например '2026-09-11', 'завтра'). Если пользователь пишет 'закинь еще...', 'добавь еще...' после обсуждения планов на завтра или другую дату, укажи эту обсуждавшуюся дату!"}
            },
            "required": ["text"]
        },
        handler=t.create_task
    )

    registry.register(
        name="get_tasks",
        description="Получает список задач пользователя за указанный диапазон дат.",
        parameters={
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "Начальная дата (YYYY-MM-DD или 'сегодня', 'завтра')"},
                "end_date": {"type": "string", "description": "Конечная дата (YYYY-MM-DD или null)"}
            },
            "required": ["start_date"]
        },
        handler=t.get_tasks
    )

    registry.register(
        name="search_tasks",
        description="Ищет задачи пользователя по ключевым словам.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Поисковый запрос / ключевые слова"}
            },
            "required": ["query"]
        },
        handler=t.search_tasks
    )

    registry.register(
        name="complete_task",
        description="Отмечает задачу как выполненную по её ID (или последнюю обсуждавшуюся задачу).",
        parameters={
            "type": "object",
            "properties": {
                "task_id": {"type": "integer", "description": "ID задачи (или null, если закрывается последняя обсуждавшаяся задача)"}
            }
        },
        handler=t.complete_task
    )

    registry.register(
        name="move_task",
        description=(
            "Переносит задачу на новую дату и при необходимости меняет указанное время. "
            "Если пользователь говорит 'перенеси её/его на завтра', можно указать ID последней обсуждавшейся задачи из контекста."
        ),
        parameters={
            "type": "object",
            "properties": {
                "task_id": {"type": "integer", "description": "ID задачи (или null, если используется последняя обсуждавшаяся задача)"},
                "target_date": {"type": "string", "description": "Новая целевая дата (YYYY-MM-DD или 'пятница', 'завтра')"},
                "new_time": {"type": "string", "description": "Новое время в формате HH:MM (если указано, или null)"}
            },
            "required": ["target_date"]
        },
        handler=t.move_task
    )

    registry.register(
        name="update_task",
        description="Обновляет текст или дату задачи по её ID.",
        parameters={
            "type": "object",
            "properties": {
                "task_id": {"type": "integer", "description": "ID задачи (или null, если используется последняя обсуждавшаяся задача)"},
                "new_text": {"type": "string", "description": "Новый текст задачи"},
                "target_date": {"type": "string", "description": "Новая дата задачи"}
            }
        },
        handler=t.update_task
    )

    registry.register(
        name="delete_task",
        description="Удаляет одну конкретную задачу по её ID (или последнюю обсуждавшуюся задачу).",
        parameters={
            "type": "object",
            "properties": {
                "task_id": {"type": "integer", "description": "ID задачи (или null, если используется последняя обсуждавшаяся задача)"}
            }
        },
        handler=t.delete_task
    )

    registry.register(
        name="clear_tasks",
        description=(
            "Массово удаляет все задачи пользователя за конкретную дату или за всё время. "
            "Внимание: требует явного подтверждения (confirmed=True) от пользователя."
        ),
        parameters={
            "type": "object",
            "properties": {
                "target_date": {"type": "string", "description": "Дата (YYYY-MM-DD) или null для всех задач вообще"},
                "confirmed": {"type": "boolean", "description": "True только если пользователь явно подтвердил удаление"}
            },
            "required": ["confirmed"]
        },
        handler=t.clear_tasks
    )

    # 2. Reminders
    registry.register(
        name="create_reminder",
        description=(
            "Создаёт однократное всплывающее Telegram-напоминание в ТОЧНОЕ время. "
            "Используй, когда пользователь просит «напомни в ...». "
            "КРИТИЧЕСКИ ВАЖНО: Если пользователь не указал конкретное время («напомни завтра»), НЕ вызывай этот инструмент наугад, а сначала спроси: «Во сколько напомнить?»."
        ),
        parameters={
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Текст напоминания"},
                "trigger_at": {"type": "string", "description": "Дата и точное время срабатывания (например '2026-09-11 15:00', '2026-09-11T15:00:00')"}
            },
            "required": ["message", "trigger_at"]
        },
        handler=t.create_reminder
    )

    registry.register(
        name="get_reminders",
        description="Получает список активных напоминаний за указанный диапазон дат.",
        parameters={
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "Начальная дата (YYYY-MM-DD или 'сегодня')"},
                "end_date": {"type": "string", "description": "Конечная дата (YYYY-MM-DD или null)"}
            },
            "required": ["start_date"]
        },
        handler=t.get_reminders
    )

    registry.register(
        name="search_reminders",
        description="Ищет напоминания пользователя по ключевым словам.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Поисковый запрос"}
            },
            "required": ["query"]
        },
        handler=t.search_reminders
    )

    registry.register(
        name="update_reminder",
        description="Обновляет время или текст напоминания по его ID (job_id).",
        parameters={
            "type": "object",
            "properties": {
                "reminder_id": {"type": "string", "description": "ID напоминания"},
                "new_trigger_at": {"type": "string", "description": "Новые дата и время (YYYY-MM-DD HH:MM)"},
                "new_message": {"type": "string", "description": "Новый текст напоминания"}
            },
            "required": ["reminder_id"]
        },
        handler=t.update_reminder
    )

    registry.register(
        name="delete_reminder",
        description="Отменяет/удаляет напоминание по его ID.",
        parameters={
            "type": "object",
            "properties": {
                "reminder_id": {"type": "string", "description": "ID напоминания"}
            },
            "required": ["reminder_id"]
        },
        handler=t.delete_reminder
    )

    # 3. Calendar
    registry.register(
        name="create_calendar_event",
        description=(
            "Создаёт встречу/событие в Google Календаре. "
            "Используй для событий, которые занимают интервал времени (приём у врача, встреча, созвон, конференция)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Название события (например: 'Встреча с Сашей')"},
                "start_time": {"type": "string", "description": "Дата и время начала в ISO формате (например '2026-09-11T15:00:00')"},
                "end_time": {"type": "string", "description": "Дата и время окончания в ISO формате (если не указано, по умолчанию +1 час)"},
                "description": {"type": "string", "description": "Описание или ссылка/детали"}
            },
            "required": ["title", "start_time"]
        },
        handler=t.create_calendar_event
    )

    registry.register(
        name="get_calendar_events",
        description="Получает список событий Google Календаря за указанный диапазон дат.",
        parameters={
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "Начальная дата (YYYY-MM-DD)"},
                "end_date": {"type": "string", "description": "Конечная дата (YYYY-MM-DD)"}
            },
            "required": ["start_date"]
        },
        handler=t.get_calendar_events
    )

    registry.register(
        name="search_calendar_events",
        description="Ищет события в Google Календаре по ключевым словам.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Поисковый запрос"}
            },
            "required": ["query"]
        },
        handler=t.search_calendar_events
    )

    registry.register(
        name="update_calendar_event",
        description="Обновляет название, время или описание события в Google Календаре по event_id.",
        parameters={
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "ID события в Google Календаре"},
                "title": {"type": "string", "description": "Новое название события"},
                "start_time": {"type": "string", "description": "Новое время начала (ISO)"},
                "end_time": {"type": "string", "description": "Новое время окончания (ISO)"},
                "description": {"type": "string", "description": "Новое описание"}
            },
            "required": ["event_id"]
        },
        handler=t.update_calendar_event
    )

    registry.register(
        name="delete_calendar_event",
        description="Удаляет событие из Google Календаря по event_id.",
        parameters={
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "ID события в Google Календаре"}
            },
            "required": ["event_id"]
        },
        handler=t.delete_calendar_event
    )

    # 4. Notes & Obsidian
    registry.register(
        name="create_note",
        description="Сохраняет постоянную текстовую заметку, пароль, факт или мысль.",
        parameters={
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Текст заметки"},
                "folder_name": {"type": "string", "description": "Название папки/раздела (если указано)"}
            },
            "required": ["content"]
        },
        handler=t.create_note
    )

    registry.register(
        name="get_notes",
        description="Получает список заметок пользователя.",
        parameters={
            "type": "object",
            "properties": {
                "folder_id": {"type": "integer", "description": "ID папки (если нужно отфильтровать)"}
            }
        },
        handler=t.get_notes
    )

    registry.register(
        name="search_notes",
        description="Ищет заметки по тексту или ключевым словам.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Поисковый запрос"}
            },
            "required": ["query"]
        },
        handler=t.search_notes
    )

    registry.register(
        name="update_note",
        description="Обновляет текст существующей заметки по ID.",
        parameters={
            "type": "object",
            "properties": {
                "note_id": {"type": "integer", "description": "ID заметки"},
                "new_content": {"type": "string", "description": "Новое содержимое заметки"}
            },
            "required": ["note_id", "new_content"]
        },
        handler=t.update_note
    )

    registry.register(
        name="delete_note",
        description="Удаляет заметку по ID.",
        parameters={
            "type": "object",
            "properties": {
                "note_id": {"type": "integer", "description": "ID заметки"}
            },
            "required": ["note_id"]
        },
        handler=t.delete_note
    )

    registry.register(
        name="append_obsidian_daily_note",
        description="Добавляет строку с задачей в ежедневную заметку Obsidian через WebDAV.",
        parameters={
            "type": "object",
            "properties": {
                "task_text": {"type": "string", "description": "Текст пункта задачи"},
                "target_date": {"type": "string", "description": "Дата заметки (YYYY-MM-DD)"},
                "target_section": {"type": "string", "description": "Заголовок секции (по умолчанию '## Задачи на сегодня')"}
            },
            "required": ["task_text"]
        },
        handler=t.append_obsidian_daily_note
    )

    # 5. Goals
    registry.register(
        name="create_goal",
        description="Создаёт цель на месяц (например: 'закрыть проект', 'прочитать 2 книги').",
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Текст цели"},
                "target_month": {"type": "string", "description": "Месяц в формате YYYY-MM (например '2026-09')"}
            },
            "required": ["text"]
        },
        handler=t.create_goal
    )

    registry.register(
        name="get_goals",
        description="Получает список целей на указанный месяц.",
        parameters={
            "type": "object",
            "properties": {
                "target_month": {"type": "string", "description": "Месяц в формате YYYY-MM (или null для текущего)"}
            }
        },
        handler=t.get_goals
    )

    registry.register(
        name="complete_goal",
        description="Переключает статус выполнения цели по ID.",
        parameters={
            "type": "object",
            "properties": {
                "goal_id": {"type": "integer", "description": "ID цели"}
            },
            "required": ["goal_id"]
        },
        handler=t.complete_goal
    )

    registry.register(
        name="delete_goal",
        description="Удаляет цель на месяц по ID.",
        parameters={
            "type": "object",
            "properties": {
                "goal_id": {"type": "integer", "description": "ID цели"}
            },
            "required": ["goal_id"]
        },
        handler=t.delete_goal
    )

    # 6. Recurring / Habits
    registry.register(
        name="create_recurring_task",
        description="Создаёт повторяющуюся задачу или привычку (каждый день, по дням недели или с интервалом).",
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Название привычки или повторяющегося действия"},
                "repeat_type": {
                    "type": "string",
                    "enum": ["daily", "weekly", "interval_days", "interval_hours", "interval_minutes"],
                    "description": "Тип повторения"
                },
                "repeat_days": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Дни недели для weekly (например ['mon', 'wed', 'fri'])"
                },
                "interval": {"type": "integer", "description": "Интервал в днях/часах/минутах"},
                "time_of_day": {"type": "string", "description": "Время суток в формате HH:MM (например '09:00')"}
            },
            "required": ["title", "repeat_type"]
        },
        handler=t.create_recurring_task
    )

    registry.register(
        name="get_recurring_tasks",
        description="Получает список всех повторяющихся задач и привычек пользователя.",
        parameters={"type": "object", "properties": {}},
        handler=t.get_recurring_tasks
    )

    registry.register(
        name="delete_recurring_task",
        description="Удаляет повторяющуюся задачу / привычку по ID.",
        parameters={
            "type": "object",
            "properties": {
                "task_id": {"type": "integer", "description": "ID повторяющейся задачи"}
            },
            "required": ["task_id"]
        },
        handler=t.delete_recurring_task
    )

    # 7. Composite tools
    registry.register(
        name="get_schedule",
        description=(
            "Получает единое агрегированное расписание (задачи на день + напоминания + события Google Календаря) "
            "на указанную дату или период. Используй, когда пользователь спрашивает: «Что у меня на сегодня/завтра?», «Какие планы?»."
        ),
        parameters={
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "Дата (YYYY-MM-DD или 'сегодня', 'завтра')"},
                "end_date": {"type": "string", "description": "Конечная дата диапазона (или null)"}
            },
            "required": ["start_date"]
        },
        handler=t.get_schedule
    )

    registry.register(
        name="search_everything",
        description=(
            "Единый сквозной поиск по всем источникам (задачи, напоминания, Google Календарь, заметки). "
            "Используй, когда пользователь спрашивает: «Когда у меня X?», «Где я записал Y?», «Найди Z»."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Поисковый запрос / название дела / ключевое слово"}
            },
            "required": ["query"]
        },
        handler=t.search_everything
    )

    return registry


default_registry = build_default_registry()
