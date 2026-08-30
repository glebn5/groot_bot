import base64
import json
import logging
import re
from datetime import date, datetime, timedelta
from typing import Optional, List
from groq import AsyncGroq
import google.generativeai as genai
from app.config import settings
from app.models.schemas import ParsedAction, TaskItem, ReminderItem
from app.utils.timezone import get_now, get_tz

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """Ты — интеллектуальный ассистент Грут (Groot), помогающий пользователю управлять задачами, событиями календаря, напоминаниями и заметками в Obsidian.

Текущая дата и время сервера: {current_datetime} (день недели: {day_of_week}).

Твоя задача — проанализировать входящее сообщение от пользователя (текстовое, расшифровку голоса или фото/документ) и вернуть строго валидный JSON по следующей Pydantic-схеме:

```json
{{
  "is_actionable": boolean,
  "is_schedule_query": boolean,
  "is_search_query": boolean (true если пользователь спрашивает "когда X?", "где X?", "найти X"),
  "search_query": "Поисковый запрос (например: 'парикмахерская', 'врач')",
  "query_date": "YYYY-MM-DD" (начальная дата запроса планов, или null),
  "query_end_date": "YYYY-MM-DD" (конечная дата если запрос на период/месяц, или null),
  "is_note_save": boolean,
  "note_content": "Текст заметки (ТОЛЬКО если пользователь просит сохранить именно заметку/пароль/информацию)",
  "is_note_query": boolean,
  "is_task_add": boolean,
  "task_text": "Текст добавляемой задачи (если задача всего 1, или null)",
  "task_date": "YYYY-MM-DD" (или null),
  "tasks": [
    {{
      "task_text": "Текст 1-й задачи",
      "task_date": "YYYY-MM-DD"
    }},
    {{
      "task_text": "Текст 2-й задачи",
      "task_date": "YYYY-MM-DD"
    }}
  ],
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
1. Расчет дат относительных дней ("сегодня", "завтра", "послезавтра", "в этот понедельник", "в следующую пятницу"):
   - Все относительные даты рассчитываются СТРОГО относительно текущей даты сервера ({current_datetime}).
   - День недели указан в параметрах сервера ({day_of_week}). Если пользователь пишет "в понедельник", "в эту среду" и т.д., выбирай ближайший СЛЕДУЮЩИЙ такой день недели относительно текущей даты сервера.
2. Все даты и месяцы ("август", "сентябрь") ВСЕГДА рассчитываются для ТЕКУЩЕГО ГОДА из серверной даты ({current_datetime}), т.е. 2026 год (НЕ 2025 и НЕ 2027)!
3. Если пользователь добавляет встречу/задачу с конкретной датой и временем (например: "В понедельник парикмахерская в 10:00"), установи `is_task_add`: true, `task_text`: "Парикмахерская в 10:00", `task_date`: дата события, а также `event_start`: дата и время начала ("YYYY-MM-DDTHH:MM:SS"), `title`: "Парикмахерская".
4. Правила расчета точного времени `trigger_at` для каждого напоминания из списка `reminders`:
   - **Относительные напоминания до события ("напоминание за час", "напоминание за 2 часа", "за 30 минут", "за день")**:
     Отсчитываются СТРОГО НАЗАД от времени начала события (`event_start`), а НЕ от текущего времени сервера!
   - **Относительные напоминания без события ("напоминание через 2 часа", "напомни через 30 минут")**:
     Отсчитываются ВПЕРЕД от текущей даты и времени сервера ({current_datetime}).
5. Разделение нескольких задач (мульти-задачи):
   - Если пользователь в одном сообщении перебирает НЕСКОЛЬКО дел/задач (списком через переносы строк, пункты 1, 2, 3 или '- ', либо в тексте типа "так, сегодня по задачам: сделать принт..., заказать штаны..."), КАТЕГОРИЧЕСКИ НЕ объединяй их в одну длинную задачу!
   - Установи `is_task_add`: true.
   - Заполни массив `tasks`, где КАЖДОЕ отдельное дело передавай отдельным объектом `{{"task_text": "...", "task_date": "YYYY-MM-DD"}}`.
   - Очищай текст каждой задачи от дефисов, номеров и от инструкций по напоминаниям в скобках. Если в скобках к конкретной задаче просят напомнить ("(напомни через 10 и 5 минут)"), добавь соответствующие элементы в массив `reminders`, а само наименование задачи оставь чистым ("Разобраться в кладовке").
6. Если пользователь запрашивает просмотр расписания/планов ("какие планы на завтра", "что у меня 3 сентября", "покажи расписание на неделю"):
   - Установи `is_schedule_query`: true.
   - Заполни `query_date` (дата, на которую запрашиваются планы). Если запрашивается диапазон, укажи `query_end_date`.
7. Квалификация заметок против задач:
   - Заметка (`is_note_save`): устанавливай `is_note_save`: true ТОЛЬКО если пользователь прямо просит сохранить заметку или запомнить факт/пароль ("запиши заметку", "запомни пинкод 4829", "добавь в заметки").
   - Задачи ("Сегодня нужно посмотреть всю одежду", "Добавь задачу...") — это ЗАДАЧИ (`is_task_add` или `obsidian_entry`), их НЕЛЬЗЯ одновременно сохранять как заметки (`is_note_save` должно быть false)!
8. Если пользователь просит перенести/переместить задачу ("перенеси задачу на завтра"), установи `is_task_move`: true, `move_task_query` (название задачи), `move_from_date` (исходная дата) и `move_to_date` (новая целевая дата).
9. Если пользователь просит удалить или очистить задачи ("убери все задачи", "удали задачи на 30 число", "очисти задачи"):
   - Для очистки списка задач (всех или за дату): установи `is_task_clear`: true, а в `clear_date` установи дату (или null если просит все задачи).
   - Для удаления конкретной задачи ("удали задачу просушить брелок"): установи `is_task_delete_single`: true, `delete_task_query`: "просушить брелок".
10. Если пользователь просит показать сохраненные заметки ("покажи заметки", "мои заметки"), установи `is_note_query`: true.
11. Поисковые запросы ("когда парикмахерская?", "когда врач?", "найти парикмахерскую"):
   - Устанавливай `is_search_query`: true, `search_query`: поисковое ключевое слово/событие (например: "парикмахерская", "врач").
12. Правило 12-часового формата времени для текущего дня ("сегодня в 8", "в 7", "сегодня в 9"):
   - Если указано время в 12-часовом формате без уточнения (например, "в 8:00") для СЕГОДНЯШНЕГО дня ({current_datetime}), и утреннее время (08:00) относительно текущей даты и времени сервера УЖЕ ПРОШЛО, а вечернее время еще в будущем, выбирай ВЕЧЕРНЕЕ время (20:00, т.е. +12 часов)!
13. Ответ ДОЛЖЕН БЫТЬ только чистым валидным JSON без разметки markdown и без комментов.
14. В confirmation_text сформируй короткое, дружелюбное и тёплое подтверждение пользователю в стиле персонажа Грута (начинай с эмодзи 🌴 или ✨, например: "🌴 Отлично, всё запомнил!", "🌴 Готово! Задачи добавил!"), без упоминания названий внешних сервисов (Obsidian/Календарь).
"""


class LLMService:
    def __init__(self):
        self.groq_client = AsyncGroq(api_key=settings.GROQ_API_KEY)
        self.text_models = [
            "openai/gpt-oss-120b",
            "openai/gpt-oss-20b",
            "qwen/qwen3.6-27b",
            "groq/compound"
        ]
        self.vision_models = [
            "openai/gpt-oss-120b"
        ]
        
        if self._is_valid_gemini_key():
            genai.configure(api_key=settings.GEMINI_API_KEY)

    def _is_valid_gemini_key(self) -> bool:
        key = settings.GEMINI_API_KEY.strip()
        return bool(key and key != "your_gemini_api_key_here" and not key.startswith("your_"))

    def _post_process_parsed_action(self, action: ParsedAction, user_text: str, now: datetime) -> ParsedAction:
        """
        Post-processes dates, search queries, 12-hour AM/PM times, and splits multiple tasks if user provided a list.
        """
        lower_text = user_text.lower()
        months_keywords = ["январ", "феврал", "март", "апрел", "мая", "май", "июн", "июл", "август", "сентябр", "октябр", "ноябр", "декабр"]
        has_explicit_month = any(m in lower_text for m in months_keywords)
        today_date = now.date()

        if not has_explicit_month:
            def adjust_date(d: Optional[date]) -> Optional[date]:
                if d and d < today_date and d.year == today_date.year and d.month == today_date.month:
                    year = d.year + (1 if d.month == 12 else 0)
                    month = 1 if d.month == 12 else d.month + 1
                    try:
                        return date(year, month, d.day)
                    except ValueError:
                        return d
                return d

            if action.query_date and not action.query_end_date:
                action.query_date = adjust_date(action.query_date)

            if action.task_date:
                action.task_date = adjust_date(action.task_date)

        # Fallback for search queries if LLM didn't set is_search_query
        if not action.is_search_query:
            clean_q = lower_text.strip()
            for prefix in ["во сколько ", "во сколько", "восколько ", "восколько", "когда ", "где ", "найти ", "покажи ", "какие ", "скажи "]:
                if clean_q.startswith(prefix):
                    clean_q = clean_q[len(prefix):].strip(" ?!.")
                    if clean_q:
                        action.is_search_query = True
                        action.search_query = clean_q
                    break

        tz = get_tz()

        def ensure_aware(dt: Optional[datetime]) -> Optional[datetime]:
            if dt is None:
                return None
            if dt.tzinfo is None:
                return dt.replace(tzinfo=tz)
            return dt.astimezone(tz)

        now = ensure_aware(now)
        has_am_keyword = any(kw in lower_text for kw in ["утра", "утром", "am", "ам"])

        # Check if user explicitly asked for reminder TODAY ("сегодня в 8")
        if "сегодня" in lower_text and action.reminders:
            for r in action.reminders:
                if r.trigger_at:
                    r.trigger_at = ensure_aware(r.trigger_at)
                    if r.trigger_at.date() != today_date:
                        r_time = r.trigger_at.time()
                        new_dt = datetime.combine(today_date, r_time, tzinfo=tz)
                        if new_dt <= now and new_dt.hour < 12 and not has_am_keyword:
                            new_dt = new_dt + timedelta(hours=12)
                        r.trigger_at = new_dt

        # Auto-adjust 12-hour AM times today to PM (hour + 12) if AM time has passed today
        if not has_am_keyword:
            if action.reminders:
                for r in action.reminders:
                    if r.trigger_at:
                        r.trigger_at = ensure_aware(r.trigger_at)
                        if r.trigger_at.date() == today_date and r.trigger_at <= now:
                            if r.trigger_at.hour < 12:
                                adjusted_dt = r.trigger_at + timedelta(hours=12)
                                if adjusted_dt > now and adjusted_dt.date() == today_date:
                                    r.trigger_at = adjusted_dt

        # --- TASK RESCHEDULING / TIME CHANGE DETECTION ---
        time_resched_match = re.search(r'(?:перенеси|перемести|поменяй|поставь)\s+(?:время\s+)?(?:на\s+)?([0-1]?\d|2[0-3])\s*:\s*([0-5]\d)', lower_text)
        if time_resched_match:
            action.is_task_move = True
            h = int(time_resched_match.group(1))
            m = time_resched_match.group(2)
            action.move_to_time = f"{h:02d}:{m}"

        if action.is_task_move and action.move_task_query:
            clean_q = re.sub(r'[\-\s]*перенеси\s+на\s+.*$', '', action.move_task_query, flags=re.IGNORECASE).strip()
            clean_q = re.sub(r'^\d{2}:\d{2}\s*—\s*', '', clean_q).strip()
            if clean_q:
                action.move_task_query = clean_q

        # --- MULTI-TASK SPLITTING AND CLEANING LOGIC ---
        if action.tasks:
            cleaned_tasks = []
            for t in action.tasks:
                if t.task_text and t.task_text.strip():
                    text = re.sub(r"^[\-\*\•\d\.\)]+\s*", "", t.task_text.strip()).strip()
                    text = re.sub(r"\((напомни|напоминание|напомнить)[^\)]*\)", "", text, flags=re.IGNORECASE).strip()
                    if text:
                        text = text[0].upper() + text[1:]
                        t_date = t.task_date or action.task_date or today_date
                        cleaned_tasks.append(TaskItem(task_text=text, task_date=t_date))
            if cleaned_tasks:
                action.tasks = cleaned_tasks
                action.is_task_add = True

        if not action.is_note_save and not action.is_schedule_query and not action.is_search_query:
            lines = [l.strip() for l in user_text.splitlines() if l.strip()]
            header_patterns = [
                r"^так,?\s*сегодня\s+по\s+задачам:?$",
                r"^по\s+задачам:?$",
                r"^задачи\s+на\s+сегодня:?$",
                r"^список\s+дел:?$",
                r"^план\s+на\s+сегодня:?$",
                r"^мои\s+задачи:?$",
                r"^сегодня:?$"
            ]

            extracted_task_lines = []
            for line in lines:
                is_header = False
                for hp in header_patterns:
                    if re.search(hp, line, re.IGNORECASE):
                        is_header = True
                        break
                if is_header:
                    continue

                clean = re.sub(r"^[\-\*\•\d\.\)]+\s*", "", line).strip()
                clean = re.sub(r"\((напомни|напоминание|напомнить)[^\)]*\)", "", clean, flags=re.IGNORECASE).strip()

                if clean:
                    clean = clean[0].upper() + clean[1:]
                    extracted_task_lines.append(clean)

            if len(extracted_task_lines) > 1:
                t_date = action.task_date or today_date
                action.tasks = [TaskItem(task_text=txt, task_date=t_date) for txt in extracted_task_lines]
                action.is_task_add = True
                action.task_text = None

        return action

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
        now = get_now()
        current_datetime_str = now.strftime("%Y-%m-%dT%H:%M:%S")
        if context_date:
            current_datetime_str += f" (Недавно обсуждавшаяся дата в диалоге: {context_date.strftime('%Y-%m-%d')})"
        
        days_ru = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
        day_of_week_str = f"{now.strftime('%A')} ({days_ru[now.weekday()]})"
        
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

        parsed_action = self._clean_and_parse_json(raw_json_str)
        return self._post_process_parsed_action(parsed_action, text_content, now)

    async def _process_text(self, sys_prompt: str, user_text: str) -> str:
        last_error = None
        for model in self.text_models:
            try:
                logger.info(f"Sending text prompt to Groq model: {model}...")
                response = await self.groq_client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": sys_prompt},
                        {"role": "user", "content": user_text}
                    ],
                    temperature=0.2,
                    response_format={"type": "json_object"}
                )
                return response.choices[0].message.content or ""
            except Exception as e:
                logger.warning(f"Groq text model '{model}' failed: {e}. Trying next model...")
                last_error = e

        if self._is_valid_gemini_key():
            try:
                logger.info("All Groq text models failed. Trying Gemini fallback...")
                return await self._process_gemini_text(sys_prompt, user_text)
            except Exception as gemini_err:
                logger.error(f"Gemini fallback failed: {gemini_err}")

        if last_error:
            raise last_error
        raise RuntimeError("Failed to process text request with LLM.")

    async def _process_vision(self, sys_prompt: str, user_text: str, image_bytes: bytes, mime_type: str) -> str:
        if self._is_valid_gemini_key():
            try:
                logger.info("Processing image with Gemini Vision...")
                return await self._process_gemini_vision(sys_prompt, user_text, image_bytes, mime_type)
            except Exception as gemini_err:
                logger.error(f"Gemini Vision failed: {gemini_err}")

        # If user provided a caption text with the image, process the caption
        if user_text and user_text.strip():
            return await self._process_text(sys_prompt, user_text)

        # Inform user how to enable image scanning
        fallback_json = {
            "is_actionable": False,
            "is_schedule_query": False,
            "is_note_save": False,
            "is_task_add": False,
            "confirmation_text": "🌴 Я получил фото! Чтобы я мог автоматически считывать с него текст (талоны к врачу, чеки, справки), укажите бесплатный **Gemini API Key** через команду `/settings`. Либо отправьте фото вместе с текстом-описанием!"
        }
        return json.dumps(fallback_json, ensure_ascii=False)

    async def _process_gemini_text(self, sys_prompt: str, user_text: str) -> str:
        prompt = sys_prompt + "\n\nСообщение пользователя:\n" + user_text
        for model_name in ["gemini-3.6-flash", "gemini-flash-latest", "gemini-3.7-flash"]:
            try:
                model = genai.GenerativeModel(model_name)
                response = await model.generate_content_async(prompt)
                return response.text or ""
            except Exception as e:
                logger.warning(f"Gemini text model '{model_name}' failed: {e}. Trying next...")
        raise RuntimeError("All Gemini text models failed.")

    async def _process_gemini_vision(self, sys_prompt: str, user_text: str, image_bytes: bytes, mime_type: str) -> str:
        prompt = sys_prompt + "\n\nПользователь прислал фото/документ (талон к врачу, чек, расписание или билет). Внимательно распознай ВСЕ записи, дату, время, имя врача/события с изображения и извлеки задачи/события календаря.\nПодпись пользователя: " + (user_text or 'без подписи')
        contents = [
            prompt,
            {"mime_type": mime_type, "data": image_bytes}
        ]
        for model_name in ["gemini-3.6-flash", "gemini-flash-latest", "gemini-3.7-flash"]:
            try:
                model = genai.GenerativeModel(model_name)
                response = await model.generate_content_async(contents)
                return response.text or ""
            except Exception as e:
                logger.warning(f"Gemini vision model '{model_name}' failed: {e}. Trying next...")
        raise RuntimeError("All Gemini vision models failed.")

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
