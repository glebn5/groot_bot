import base64
import json
import logging
from datetime import datetime
from typing import Optional
from groq import AsyncGroq
import google.generativeai as genai
from app.config import settings
from app.models.schemas import ParsedAction

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """Ты — интеллектуальный ассистент Грут (Groot), помогающий пользователю управлять задачами, событиями календаря, напоминаниями и заметками в Obsidian.

Текущая дата и время сервера: {current_datetime} (день недели: {day_of_week}).

Твоя задача — проанализировать входящее сообщение от пользователя (текстовое, расшифровку голоса или фото/документ) и вернуть строго валидный JSON по следующей Pydantic-схеме:

```json
{{
  "is_actionable": boolean,
  "title": "Краткое название действия/запроса",
  "description": "Подробности или описание (при наличии)",
  "event_start": "YYYY-MM-DDTHH:MM:SS" (или null),
  "event_end": "YYYY-MM-DDTHH:MM:SS" (или null),
  "reminders": [
    {{
      "trigger_at": "YYYY-MM-DDTHH:MM:SS",
      "message": "Текст напоминания"
    }}
  ],
  "obsidian_entry": {{
    "date": "YYYY-MM-DD",
    "target_section": "## Задачи на сегодня",
    "task_text": "Текст задачи (например: '15:30 Встреча с коллегами')"
  }} (или null),
  "confirmation_text": "Дружелюбный отклик пользователю с подтверждением созданных задач, событий и напоминаний"
}}
```

Правила извлечения:
1. Относительные даты ("сегодня", "завтра", "в следующую среду", "через 2 часа") рассчитываются строго от текущего времени сервера ({current_datetime}).
2. Если указано время для задачи без даты, по умолчанию дата — сегодня.
3. В `obsidian_entry`:
   - `target_section` по умолчанию "## Задачи на сегодня" (или подходящая секция из шаблона).
   - `task_text` формируется лаконично.
4. В `reminders`: `trigger_at` должно быть точным моментом времени в будущем.
5. Ответ ДОЛЖЕН БЫТЬ только чистым валидным JSON без разметки markdown и без комментов.
"""


class LLMService:
    def __init__(self):
        self.groq_client = AsyncGroq(api_key=settings.GROQ_API_KEY)
        self.text_model = "llama-3.3-70b-versatile"
        self.vision_model = "llama-3.2-11b-vision-preview"
        
        if settings.GEMINI_API_KEY:
            genai.configure(api_key=settings.GEMINI_API_KEY)

    async def parse_user_request(
        self,
        text_content: str,
        image_bytes: Optional[bytes] = None,
        mime_type: str = "image/jpeg"
    ) -> ParsedAction:
        """
        Parse user request (text or image) into structured ParsedAction using Groq or Gemini API.
        """
        now = datetime.now()
        current_datetime_str = now.strftime("%Y-%m-%dT%H:%M:%S")
        day_of_week_str = now.strftime("%A")
        
        sys_prompt = SYSTEM_PROMPT.format(
            current_datetime=current_datetime_str,
            day_of_week=day_of_week_str
        )

        raw_json_str = ""

        if image_bytes:
            # Vision request
            raw_json_str = await self._process_vision(sys_prompt, text_content, image_bytes, mime_type)
        else:
            # Text request
            raw_json_str = await self._process_text(sys_prompt, text_content)

        return self._clean_and_parse_json(raw_json_str)

    async def _process_text(self, sys_prompt: str, user_text: str) -> str:
        try:
            logger.info("Sending text prompt to Groq LLM...")
            response = await self.groq_client.chat.completions.create(
                model=self.text_model,
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_text}
                ],
                temperature=0.2,
                response_format={"type": "json_object"}
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            logger.warning(f"Groq LLM text call failed: {e}. Trying fallback if available...", exc_info=True)
            if settings.GEMINI_API_KEY:
                return await self._process_gemini_text(sys_prompt, user_text)
            raise e

    async def _process_vision(self, sys_prompt: str, user_text: str, image_bytes: bytes, mime_type: str) -> str:
        # Try Groq Vision model first
        try:
            logger.info("Sending vision prompt to Groq Vision LLM...")
            base64_image = base64.b64encode(image_bytes).decode('utf-8')
            image_url = f"data:{mime_type};base64,{base64_image}"
            
            prompt_msg = user_text if user_text else "Распознай текст/информацию на этом фото и извлеки задачи, даты, время и события."
            
            response = await self.groq_client.chat.completions.create(
                model=self.vision_model,
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt_msg},
                            {"type": "image_url", "image_url": {"url": image_url}}
                        ]
                    }
                ],
                temperature=0.2,
                response_format={"type": "json_object"}
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            logger.warning(f"Groq Vision failed: {e}. Falling back to Gemini Flash if configured...", exc_info=True)
            if settings.GEMINI_API_KEY:
                return await self._process_gemini_vision(sys_prompt, user_text, image_bytes, mime_type)
            raise e

    async def _process_gemini_text(self, sys_prompt: str, user_text: str) -> str:
        model = genai.GenerativeModel("gemini-1.5-flash")
        prompt = f"{sys_prompt}\n\nСообщение пользователя:\n{user_text}"
        response = await model.generate_content_async(prompt)
        return response.text or ""

    async def _process_gemini_vision(self, sys_prompt: str, user_text: str, image_bytes: bytes, mime_type: str) -> str:
        model = genai.GenerativeModel("gemini-1.5-flash")
        prompt = f"{sys_prompt}\n\nСообщение пользователя:\n{user_text or 'Распознай задачи/события с изображения.'}"
        contents = [
            prompt,
            {"mime_type": mime_type, "data": image_bytes}
        ]
        response = await model.generate_content_async(contents)
        return response.text or ""

    def _clean_and_parse_json(self, raw_str: str) -> ParsedAction:
        cleaned = raw_str.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        if cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

        data_dict = json.loads(cleaned)
        return ParsedAction.model_validate(data_dict)


llm_service = LLMService()
