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


class LLMProvider:
    """
    Unified LLM provider supporting tool-calling with Groq primary and Gemini fallback.
    """
    def __init__(self):
        self.groq_client = AsyncGroq(api_key=settings.GROQ_API_KEY, max_retries=1)
        self.groq_models = [
            "llama-3.3-70b-versatile",
            "openai/gpt-oss-120b",
            "openai/gpt-oss-20b",
            "qwen/qwen3.6-27b",
            "groq/compound"
        ]
        self.gemini_models = [
            "gemini-3.6-flash",
            "gemini-flash-latest",
            "gemini-3.7-flash"
        ]
        if self._is_valid_gemini_key():
            genai.configure(api_key=settings.GEMINI_API_KEY)

    def _is_valid_gemini_key(self) -> bool:
        key = settings.GEMINI_API_KEY.strip()
        return bool(key and key != "your_gemini_api_key_here" and not key.startswith("your_"))

    async def generate_turn(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None
    ) -> LLMTurnResponse:
        """
        Sends messages and available tools to the LLM.
        Returns text content and/or list of requested tool calls.
        """
        last_error = None

        # 1. Try Groq models with native function calling
        for model in self.groq_models:
            try:
                logger.info(f"Sending turn to Groq model '{model}' with {len(tools or [])} tools...")
                kwargs: Dict[str, Any] = {
                    "model": model,
                    "messages": messages,
                    "temperature": 0.2
                }
                if tools:
                    kwargs["tools"] = tools
                    kwargs["tool_choice"] = "auto"

                response = await self.groq_client.chat.completions.create(**kwargs)
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
            except Exception as e:
                logger.warning(f"Groq model '{model}' failed: {e}. Trying next...")
                last_error = e

        # 2. Fallback to Gemini if Groq is unavailable
        if self._is_valid_gemini_key():
            try:
                logger.info("Falling back to Gemini for agent turn...")
                return await self._generate_gemini_turn(messages, tools)
            except Exception as gemini_err:
                logger.error(f"Gemini fallback failed: {gemini_err}", exc_info=True)
                last_error = gemini_err

        if last_error:
            raise last_error
        raise RuntimeError("No LLM provider was able to process the request.")

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
