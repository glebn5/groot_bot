import json
import logging
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from groq import AsyncGroq
import google.generativeai as genai
from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class ToolCallRequest:
    id: str
    name: str
    arguments: Dict[str, Any]


@dataclass
class LLMTurnResponse:
    content: Optional[str] = None
    tool_calls: List[ToolCallRequest] = field(default_factory=list)


from app.agent.ai_manager import ai_request_manager

class LLMProvider:
    """
    Unified LLM provider supporting tool-calling with Groq primary and Gemini fallback,
    orchestrated through AIRequestManager for rate-limiting, cooldowns, and concurrency.
    """
    def __init__(self):
        self.ai_manager = ai_request_manager
        self.gemini_models = ai_request_manager.GEMINI_MODELS

    def _is_valid_gemini_key(self) -> bool:
        return self.ai_manager.is_gemini_available()

    async def generate_turn(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None
    ) -> LLMTurnResponse:
        """
        Sends messages and available tools to the LLM via AIRequestManager.
        Returns text content and/or list of requested tool calls.
        """
        response, model_used = await self.ai_manager.execute_tool_turn(messages=messages, tools=tools)

        if model_used == "gemini-fallback":
            return LLMTurnResponse(content=str(response), tool_calls=[])

        choice = response.choices[0]
        msg = choice.message

        tool_calls: List[ToolCallRequest] = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                try:
                    args_dict = json.loads(tc.function.arguments) if isinstance(tc.function.arguments, str) else (tc.function.arguments or {})
                except Exception as parse_err:
                    logger.warning(f"Failed to parse tool arguments as JSON: {tc.function.arguments} ({parse_err})")
                    args_dict = {}

                tool_calls.append(ToolCallRequest(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=args_dict
                ))

        return LLMTurnResponse(
            content=msg.content or "",
            tool_calls=tool_calls
        )


    async def _generate_gemini_turn(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None
    ) -> LLMTurnResponse:
        """
        Gemini fallback with prompt-based tool execution when native calls are routed.
        """
        # Build prompt from conversation messages
        full_prompt_lines = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if role == "system":
                full_prompt_lines.append(f"System instructions:\n{content}\n")
            elif role == "user":
                full_prompt_lines.append(f"User: {content}")
            elif role == "assistant":
                full_prompt_lines.append(f"Assistant: {content}")
            elif role == "tool":
                full_prompt_lines.append(f"Tool Result ({m.get('name', 'tool')}): {content}")

        prompt_str = "\n\n".join(full_prompt_lines)

        for model_name in self.gemini_models:
            try:
                model = genai.GenerativeModel(model_name)
                response = await model.generate_content_async(prompt_str)
                text = response.text or ""
                return LLMTurnResponse(content=text, tool_calls=[])
            except Exception as e:
                logger.warning(f"Gemini model '{model_name}' failed: {e}")

        raise RuntimeError("All Gemini fallback models failed.")

    async def extract_vision_text(
        self,
        image_bytes: bytes,
        mime_type: str = "image/jpeg",
        user_caption: str = ""
    ) -> Optional[str]:
        """
        Extracts textual information (dates, times, events, items, doctor/meeting details)
        from an image using Gemini Vision.
        """
        if not self._is_valid_gemini_key():
            return None

        prompt = (
            "Внимательно распознай и подробно перечисли ВСЕ записи, дату, время, имя врача/события, "
            "место и текст с этого изображения (талон к врачу, чек, справка, расписание, билет или заметка).\n"
            f"Комментарий пользователя к фото: {user_caption or 'без комментария'}"
        )
        contents = [
            prompt,
            {"mime_type": mime_type, "data": image_bytes}
        ]

        for model_name in self.gemini_models:
            try:
                model = genai.GenerativeModel(model_name)
                response = await model.generate_content_async(contents)
                if response.text:
                    return response.text.strip()
            except Exception as e:
                logger.warning(f"Gemini vision model '{model_name}' failed: {e}")

        return None


llm_provider = LLMProvider()
