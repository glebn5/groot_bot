import json
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any

from app.config import settings
from app.agent.schemas import ToolExecutionContext, ToolResult, AgentResponse
from app.agent.prompts import GROOT_SYSTEM_PROMPT
from app.agent.registry import ToolRegistry, default_registry
from app.agent.providers import LLMProvider, llm_provider, ToolCallRequest
from app.services.context import context_service
from app.utils.timezone import get_now

logger = logging.getLogger("app.agent")


class GrootAgent:
    """
    Main conversational AI Agent orchestrating multi-turn dialog,
    intent understanding, tool calling loops, and natural language responses.
    """
    def __init__(
        self,
        registry: Optional[ToolRegistry] = None,
        provider: Optional[LLMProvider] = None
    ):
        self.registry = registry or default_registry
        self.provider = provider or llm_provider
        self.max_iterations = getattr(settings, "AGENT_MAX_ITERATIONS", 8)

    async def process_message(
        self,
        user_id: int,
        chat_id: int,
        user_text: str
    ) -> AgentResponse:
        """
        Processes incoming user message through the agent loop.
        """
        clean_text = (user_text or "").strip()
        logger.info(f"[agent.request] user_id={user_id} text='{clean_text}'")

        exec_context = ToolExecutionContext(user_id=user_id, chat_id=chat_id)
        now = get_now()
        days_ru = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
        day_str = f"{now.strftime('%A')} ({days_ru[now.weekday()]})"

        # 1. Check for confirmation of pending destructive actions
        pending = context_service.get_pending_confirmation(user_id)
        if pending:
            import re
            lower_input = clean_text.lower()
            is_confirm = bool(re.search(r'\b(да|yes|подтверждаю|подтвердить|удали|удалить|точно|очисти|очистить)\b', lower_input))
            is_cancel = bool(re.search(r'\b(нет|no|отмена|отменить|не надо|стоп|cancel)\b', lower_input))

            if is_confirm and not is_cancel:
                logger.info(f"User confirmed pending action: {pending}")
                context_service.clear_pending_confirmation(user_id)
                action_name = pending.get("action")

                if action_name == "clear_tasks":
                    res = await self.registry.execute(
                        "clear_tasks",
                        {"target_date": pending.get("target_date"), "confirmed": True},
                        exec_context
                    )
                    reply = f"🌴 {res.message}"
                    context_service.add_message(user_id, "user", clean_text)
                    context_service.add_message(user_id, "assistant", reply)
                    return AgentResponse(reply_text=reply, tool_calls_count=1, executed_tools=[res.to_dict()])
            elif any(w == lower_input or lower_input.startswith(w + " ") for w in cancel_words):
                logger.info("User cancelled pending action.")
                context_service.clear_pending_confirmation(user_id)
                reply = "🌴 Действие отменено. Ничего не удалено!"
                context_service.add_message(user_id, "user", clean_text)
                context_service.add_message(user_id, "assistant", reply)
                return AgentResponse(reply_text=reply)

        # 2. Build system prompt and context
        context_summary = context_service.get_context_summary(user_id)
        system_prompt = GROOT_SYSTEM_PROMPT.format(
            current_datetime=now.strftime("%Y-%m-%d %H:%M:%S"),
            day_of_week=day_str,
            timezone=settings.TIMEZONE,
            context_summary=context_summary
        )

        # 3. Retrieve conversation history
        history = context_service.get_messages(user_id, limit=settings.AGENT_HISTORY_LIMIT * 2)

        # Prepare messages array for LLM
        active_messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt}
        ]
        for h in history:
            m_dict = {
                "role": h.get("role", "user"),
                "content": h.get("content", "")
            }
            if h.get("tool_calls"):
                m_dict["tool_calls"] = h["tool_calls"]
            if h.get("tool_call_id"):
                m_dict["tool_call_id"] = h["tool_call_id"]
            if h.get("name"):
                m_dict["name"] = h["name"]
            active_messages.append(m_dict)

        # Append current user prompt
        active_messages.append({"role": "user", "content": clean_text})
        context_service.add_message(user_id, "user", clean_text)

        # 4. Agent Reasoning Loop
        executed_tools: List[Dict[str, Any]] = []
        tools_spec = self.registry.get_openai_tools()

        for iteration in range(self.max_iterations):
            logger.info(f"Agent turn iteration {iteration + 1}/{self.max_iterations}")
            try:
                turn = await self.provider.generate_turn(messages=active_messages, tools=tools_spec)
            except Exception as e:
                logger.error(f"Error communicating with LLM: {e}", exc_info=True)
                return AgentResponse(
                    reply_text="⚠️ Сейчас AI недоступен. Попробуйте воспользоваться кнопками меню или повторить запрос позже.",
                    executed_tools=executed_tools
                )

            # If LLM returned text without tool calls, we're done!
            if not turn.tool_calls:
                final_text = (turn.content or "").strip()
                if not final_text:
                    final_text = "🌴 Готово!"

                logger.info(f"[agent.final_response] '{final_text}'")
                context_service.add_message(user_id, "assistant", final_text)
                return AgentResponse(
                    reply_text=final_text,
                    tool_calls_count=len(executed_tools),
                    executed_tools=executed_tools
                )

            # Record assistant turn with tool calls in history
            assistant_msg: Dict[str, Any] = {
                "role": "assistant",
                "content": turn.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, ensure_ascii=False)
                        }
                    }
                    for tc in turn.tool_calls
                ]
            }
            active_messages.append(assistant_msg)
            context_service.add_message(
                user_id=user_id,
                role="assistant",
                content=turn.content or "",
                tool_calls=assistant_msg["tool_calls"]
            )

            # Execute requested tools
            for tc in turn.tool_calls:
                logger.info(f"[agent.tool_call] {tc.name}({tc.arguments})")
                tool_result = await self.registry.execute(tc.name, tc.arguments, exec_context)
                logger.info(f"[agent.tool_result] {tc.name} -> success={tool_result.success}")

                executed_tools.append({
                    "name": tc.name,
                    "arguments": tc.arguments,
                    "result": tool_result.to_dict()
                })

                # Check if confirmation is required
                if tool_result.error_code == "confirmation_required":
                    context_service.set_pending_confirmation(user_id, tool_result.data or {"action": tc.name})
                    reply_text = f"🌴 {tool_result.message}"
                    context_service.add_message(user_id, "assistant", reply_text)
                    return AgentResponse(
                        reply_text=reply_text,
                        tool_calls_count=len(executed_tools),
                        executed_tools=executed_tools,
                        requires_confirmation=True,
                        pending_action=tool_result.data
                    )

                result_json_str = json.dumps(tool_result.to_dict(), ensure_ascii=False)
                active_messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": tc.name,
                    "content": result_json_str
                })
                context_service.add_message(
                    user_id=user_id,
                    role="tool",
                    content=result_json_str,
                    tool_call_id=tc.id,
                    name=tc.name
                )

        # Fallback if max iterations exceeded
        logger.warning(f"Agent reached max iterations limit ({self.max_iterations}). Returning summary.")
        fallback_text = "🌴 Действия выполнены!"
        if executed_tools:
            details = [f"- {t['name']}: {t['result'].get('message', 'ok')}" for t in executed_tools]
            fallback_text += "\n" + "\n".join(details)
        context_service.add_message(user_id, "assistant", fallback_text)

        return AgentResponse(
            reply_text=fallback_text,
            tool_calls_count=len(executed_tools),
            executed_tools=executed_tools
        )


groot_agent = GrootAgent()
