import base64
import json
import logging
from datetime import date, datetime
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
  "is_schedule_query": boolean,
  "query_date": "YYYY-MM-DD" (начальная дата запроса планов, или null),
  "query_end_date": "YYYY-MM-DD" (конечная дата если запрос на период/месяц, или null),
  "is_note_save": boolean,
  "note_content": "Текст заметки (ТОЛЬКО если пользователь просит сохранить именно заметку/пароль/информацию)",
  "is_note_query": boolean,
  "is_task_add": boolean,
  "task_text": "Текст добавляемой задачи (или null)",
  "task_date": "YYYY-MM-DD" (или null),
  "is_task_move": boolean,
  "move_task_query": "Ключевые слова перемещаемой задачи (или null)",
  "move_from_date": "YYYY-MM-DD" (или null),
  "move_to_date": "YYYY-MM-DD" (или null),
  "is_task_clear": boolean,
  "clear_date": "YYYY-MM-DD" (или null),
  "is_task_delete_single": boolean,
  "delete_task_query": "Ключевые слова удаляемой задачи (или null)",
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
  "confirmation_text": "Дружелюбный отклик пользователю с подтверждением"
}}
```

Правила извлечения:
1. Относительные даты ("сегодня", "завтра", "в следующую среду", "через 2 часа") рассчитываются строго от текущей даты сервера ({current_datetime}).
2. Все даты и месяцы ("август", "сентябрь") ВСЕГДА рассчитываются для ТЕКУЩЕГО ГОДА из серверной даты ({current_datetime}), т.е. 2026 год (НЕ 2025 и НЕ 2027)!
3. Если указано время для задачи без даты, по умолчанию дата — сегодня.
4. В `reminders`: `trigger_at` должно быть точным моментом времени в будущем.
5. Запросы планов и расписания (`is_schedule_query`):
   - Если пользователь спрашивает про конкретный день ("планы на сегодня", "что 3 сентября"): `query_date` = дата того дня, `query_end_date` = null.
   - Если пользователь спрашивает про целый месяц ("планы на август", "что у нас на август"): `query_date` = "2026-08-01", `query_end_date` = "2026-08-31".
6. Квалификация заметок против задач:
   - Заметка (`is_note_save`): устанавливай `is_note_save`: true ТОЛЬКО если пользователь прямо просит сохранить заметку или запомнить факт/пароль ("запиши заметку", "запомни пинкод 4829", "добавь в заметки").
   - Задачи ("Сегодня нужно посмотреть всю одежду", "Добавь задачу...") — это ЗАДАЧИ (`is_task_add` или `obsidian_entry`), их НЕЛЬЗЯ одновременно сохранять как заметки (`is_note_save` должно быть false)!
7. Если пользователь просит перенести/переместить задачу ("У меня есть задача на 26 число поменять постельное, перемести её на 28 число" или "перенеси задачу на завтра"), установи `is_task_move`: true, `move_task_query` (название задачи), `move_from_date` (исходная дата) и `move_to_date` (новая целевая дата).
8. Если пользователь просит удалить или очистить задачи ("убери все задачи", "удали задачи на 30 число", "очисти задачи"):
   - Для очистки списка задач (всех или за дату): установи `is_task_clear`: true, а в `clear_date` установи дату (или null если просит все задачи).
   - Для удаления конкретной задачи ("удали задачу просушить брелок"): установи `is_task_delete_single`: true, `delete_task_query`: "просушить брелок".
9. Если пользователь просит показать сохраненные заметки ("покажи заметки", "мои заметки"), установи `is_note_query`: true.
10. Ответ ДОЛЖЕН БЫТЬ только чистым валидным JSON без разметки markdown и без комментов.
11. В confirmation_text сформируй короткое подтверждение без упоминания внешних сервисов (Obsidian/Календарь).
"""


class LLMService:
    def __init__(self):
        self.groq_client = AsyncGroq(api_key=settings.GROQ_API_KEY)
        self.text_model = "openai/gpt-oss-120b"
        self.vision_model = "openai/gpt-oss-120b"
        
        if self._is_valid_gemini_key():
            genai.configure(api_key=settings.GEMINI_API_KEY)

    def _is_valid_gemini_key(self) -> bool:
        key = settings.GEMINI_API_KEY.strip()
        return bool(key and key != "your_gemini_api_key_here" and not key.startswith("your_"))

    async def parse_user_request(
        self,
        text_content: str,
        image_bytes: Optional[bytes] = None,
        mime_type: str = "image/jpeg",
        context_date: Optional[date] = None
    ) -> ParsedAction:
        """
        Parse user request (text or image) into structured ParsedAction using Groq or Gemini API.
        """
        now = datetime.now()
        current_datetime_str = now.strftime("%Y-%m-%dT%H:%M:%S")
        if context_date:
            current_datetime_str += f" (Недавно обсуждавшаяся дата в диалоге: {context_date.strftime('%Y-%m-%d')})"
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
            if self._is_valid_gemini_key():
                try:
                    return await self._process_gemini_text(sys_prompt, user_text)
                except Exception as gemini_err:
                    logger.error(f"Gemini fallback failed: {gemini_err}")
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
            if self._is_valid_gemini_key():
                try:
                    return await self._process_gemini_vision(sys_prompt, user_text, image_bytes, mime_type)
                except Exception as gemini_err:
                    logger.error(f"Gemini Vision fallback failed: {gemini_err}")
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
