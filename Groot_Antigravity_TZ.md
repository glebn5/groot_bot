# Groot Bot — ТЗ для Antigravity: переход от LLM-парсера к полноценному AI-ассистенту

## 0. Главная задача

Ты работаешь с существующим production-oriented Telegram-ботом `glebn5/groot_bot`.

Главная цель этой задачи:

> Переделать AI-слой Groot с модели `сообщение пользователя → огромный JSON ParsedAction → жёсткий if/elif pipeline` на модель `сообщение → AI Agent → выбор инструмента → выполнение инструмента → результат инструмента → продолжение рассуждения/следующего действия → естественный ответ пользователю`.

Важно: это НЕ задача переписать весь бот с нуля.

Нужно сохранить существующие функции, данные, Telegram UI, SQLite, APScheduler, Google Calendar, Obsidian/WebDAV, голос, vision и существующие ручные кнопки. Основная архитектурная переделка должна происходить вокруг AI orchestration.

Репозиторий:
https://github.com/glebn5/groot_bot

Перед изменениями обязательно проанализируй фактическое состояние всех файлов репозитория. Не полагайся только на это ТЗ, если код уже изменился.

---

# 1. Что сейчас есть

Проект — личный мультимодальный Telegram-ассистент на:

- Python 3.11
- aiogram 3.x
- Pydantic 2.x
- Groq API
- Gemini
- Whisper STT
- APScheduler
- SQLite
- Google Calendar API
- WebDAV/Obsidian
- Docker/Docker Compose

Основные директории:

```text
app/
├── main.py
├── config.py
├── middleware/
│   └── auth.py
├── models/
│   └── schemas.py
├── services/
│   ├── calendar.py
│   ├── context.py
│   ├── goals.py
│   ├── llm.py
│   ├── notes.py
│   ├── obsidian.py
│   ├── recurring.py
│   ├── scheduler.py
│   ├── stt.py
│   └── tasks.py
├── handlers/
│   ├── common.py
│   ├── goals.py
│   ├── habits.py
│   ├── media.py
│   ├── notes.py
│   ├── settings.py
│   ├── text.py
│   └── voice.py
└── utils/
    ├── keyboard.py
    └── timezone.py
```

Существующие сервисы уже покрывают большую часть нужных возможностей. Их нельзя без необходимости заменять.

---

# 2. Главная архитектурная проблема

Сейчас `app/services/llm.py` использует один огромный `SYSTEM_PROMPT`.

LLM обязана вернуть объект `ParsedAction`, в котором десятки флагов:

```text
is_actionable
is_schedule_query
is_search_query
is_note_save
is_note_query
is_task_add
is_task_move
is_task_clear
is_task_delete_single
is_goal_add
is_goals_query
is_recurring_add
is_recurring_query
...
```

Плюс поля:

```text
task_text
task_date
tasks
move_task_query
move_from_date
move_to_date
move_to_time
clear_date
delete_task_query
goal_text
target_month
recurring_title
repeat_type
repeat_days
repeat_interval
repeat_time
title
description
event_start
event_end
reminders
obsidian_entry
confirmation_text
```

Это делает модель не настоящим агентом, а классификатором/парсером.

Далее `execute_action_pipeline()` в `app/handlers/text.py` вручную проверяет эти флаги:

```python
if action.is_note_query:
    ...

if action.is_goal_add:
    ...

if action.is_recurring_add:
    ...

if action.is_search_query:
    ...

if action.is_task_move:
    ...

if action.is_task_clear:
    ...

if action.is_task_delete_single:
    ...

if action.is_schedule_query:
    ...

if action.is_task_add:
    ...
```

Это и нужно изменить.

---

# 3. Что должно получиться

Новая архитектура:

```text
Telegram message
        ↓
Input preprocessing
        ↓
AI Agent
        ↓
понимание намерения + контекста
        ↓
выбор tool
        ↓
Tool execution
        ↓
результат tool
        ↓
AI Agent решает:
  - нужен ещё один tool?
  - нужно задать вопрос?
  - можно ответить пользователю?
        ↓
Final natural-language response
```

Пример:

Пользователь:

> Завтра у меня стоматолог в 15, напомни за час и добавь в календарь.

AI должен понять, что здесь нужно выполнить ДВА действия:

```text
1. Создать календарное событие:
   стоматолог
   завтра 15:00
   продолжительность по умолчанию 1 час

2. Создать Telegram reminder:
   завтра 14:00
   "Стоматолог"
```

Никакой необходимости заполнять один огромный JSON.

---

# 4. Агент должен поддерживать цепочку действий

Это критически важно.

Не ограничивать агента одним tool call.

Должно быть возможно:

```text
User
 ↓
Agent
 ↓
get_tasks()
 ↓
Tool result
 ↓
Agent
 ↓
edit_task()
 ↓
Tool result
 ↓
Agent
 ↓
final response
```

Или:

```text
User:
"Завтра встреча с Сашей в 16:00, поставь в календарь и напомни за 2 часа"

Agent
 ├── create_calendar_event()
 └── create_reminder()
```

Или:

```text
User:
"Что у меня завтра?"

Agent
 └── get_schedule(date=tomorrow)
        ↓
      result
        ↓
      final response
```

---

# 5. Контекст диалога

Текущий `app/services/context.py` хранит практически только последнюю дату:

```python
user_id -> {
    "last_date": date,
    "updated_at": datetime
}
```

Этого недостаточно для нормального ассистента.

Нужно добавить краткосрочную conversational memory.

Минимально хранить:

```text
user_id
conversation/message history
last referenced task
last referenced reminder
last referenced calendar event
last referenced note
last referenced date
last tool results / relevant entities
updated_at
```

Но не хранить бесконечную историю.

Предпочтительно:

- последние N сообщений;
- либо ограничение по токенам;
- либо summary + последние сообщения.

Контекст должен быть привязан к пользователю.

---

# 6. Примеры, которые ОБЯЗАТЕЛЬНО должны работать

## 6.1 Создание

User:

> Напомни завтра в 15 позвонить Саше.

Agent:

```text
create_reminder(
    text="Позвонить Саше",
    datetime="..."
)
```

Final:

> 🌴 Готово! Напомню завтра в 15:00 позвонить Саше.

---

## 6.2 Уточнение

User:

> Напомни завтра позвонить Саше.

Agent не должен выдумывать время.

Он должен спросить:

> Во сколько завтра напомнить?

После:

> В 15.

Agent должен использовать контекст предыдущего сообщения и создать reminder.

---

## 6.3 Контекст

User:

> Напомни завтра позвонить Саше.

Bot:

> Во сколько?

User:

> После обеда.

Agent должен понять, что это ответ на вопрос о времени предыдущего reminder.

Если система не может однозначно выбрать время, спросить:

> Во сколько поставить? Например, в 15:00.

Не создавать случайное время без основания.

---

## 6.4 Изменение

User:

> Перенеси звонок Саше на пятницу.

Agent:

1. Находит соответствующую задачу/reminder.
2. Если найден ровно один объект — переносит.
3. Если найдено несколько — показывает варианты или уточняет.
4. Если не найдено — честно сообщает.

---

## 6.5 Отмена

User:

> Отмени напоминание про Сашу.

Agent:

1. Ищет reminders.
2. Если один — удаляет.
3. Если несколько — уточняет.

---

# 7. Не использовать опасное fuzzy matching без проверки

Сейчас `tasks_service.move_task()` и `search_tasks()` используют поиск по словам.

Это можно оставить как механизм поиска, но AI должен получать результаты поиска перед изменением.

НЕЛЬЗЯ:

```text
User: "Перенеси задачу"

Agent → random task → modify
```

Правильно:

```text
Agent → search_tasks()
Tool → [task 41, task 58]
Agent → "Какую именно?"
```

Если найден ровно один объект — можно продолжить автоматически.

---

# 8. Инструменты агента

Создать отдельный слой:

```text
app/agent/
├── __init__.py
├── agent.py
├── tools.py
├── registry.py
├── prompts.py
├── context.py
└── schemas.py
```

Или аналогичную структуру, если она лучше соответствует существующему проекту.

Не помещать всю агентскую логику в `handlers/text.py`.

---

# 9. Tool: задачи

Нужны tools примерно такого уровня.

## create_task

```text
create_task(
    text: str,
    target_date: date | null
)
```

Использует:

```python
tasks_service.add_task()
```

## get_tasks

```text
get_tasks(
    start_date: date,
    end_date: date | null
)
```

Использует:

```python
tasks_service.get_tasks_for_date_range()
```

## search_tasks

```text
search_tasks(
    query: str,
    target_date: date | null
)
```

Использует существующий service.

## move_task

Лучше делать через ID после поиска:

```text
move_task(
    task_id: int,
    target_date: date | null,
    new_time: str | null
)
```

Если существующий service требует query — можно добавить безопасный `move_task_by_id_with_time()`.

## complete_task

```text
complete_task(task_id)
```

## delete_task

```text
delete_task(task_id)
```

## update_task

```text
update_task(
    task_id,
    new_text?,
    target_date?,
    new_time?
)
```

## clear_tasks

```text
clear_tasks(target_date?)
```

Для `clear_tasks` желательно добавить confirmation policy.

Например:

> Удалить ВСЕ задачи за 12 сентября?

Не выполнять потенциально массовое удаление без достаточной уверенности.

---

# 10. Tool: напоминания

Создать:

```text
create_reminder(
    message: str,
    trigger_at: datetime
)

get_reminders(
    start_date,
    end_date
)

search_reminders(
    query
)

update_reminder(
    reminder_id,
    trigger_at?,
    message?
)

delete_reminder(
    reminder_id
)

snooze_reminder(
    reminder_id,
    minutes
)
```

Использовать существующий `scheduler_service`.

Не ломать APScheduler/SQLite JobStore.

Важно: tool должен проверять, что reminder принадлежит текущему пользователю.

Существующая архитектура использует `chat_id` в args job'а. При изменении/удалении обязательно сохранять user ownership.

---

# 11. Tool: календарь

Использовать существующий `calendar_service`.

Минимум:

```text
create_calendar_event(
    title,
    start_time,
    end_time?,
    description?
)

search_calendar_events(
    query
)

get_calendar_events(
    start_date,
    end_date
)

update_calendar_event(...)
delete_calendar_event(...)
```

Если существующий `calendar.py` не поддерживает update/delete/get range — аккуратно добавить эти методы.

Не ломать Google Service Account.

Если Calendar не настроен:

- tool возвращает структурированную ошибку;
- агент сообщает пользователю;
- не притворяется, что событие создано.

---

# 12. Tool: заметки

Использовать:

```python
notes_service
```

Tools:

```text
create_note(content, folder?)
search_notes(query?)
get_notes(...)
update_note(...)
delete_note(...)
```

Если существующий notes service не поддерживает часть операций — добавить их.

Важно отличать:

```text
"запомни, что..."
```

от:

```text
"поставь задачу..."
```

и от:

```text
"добавь событие..."
```

Но теперь это должно быть решением агента, а не набором boolean-флагов.

---

# 13. Tool: цели

Использовать `goals_service`.

Tools:

```text
create_goal(
    text,
    month
)

get_goals(month)

complete_goal(goal_id)

delete_goal(goal_id)
```

Если методы уже существуют — обернуть их, не дублировать бизнес-логику.

---

# 14. Tool: привычки / recurring

Использовать `recurring_service`.

Tools:

```text
create_recurring_task(
    title,
    repeat_type,
    repeat_days?,
    interval?,
    time?
)

get_recurring_tasks()

update_recurring_task(...)

delete_recurring_task(...)
```

После создания не забывать существующую интеграцию:

```python
scheduler_service.schedule_recurring_task_job(task)
```

---

# 15. Tool: расписание

Создать высокоуровневый tool:

```text
get_schedule(
    start_date,
    end_date
)
```

Он должен агрегировать:

- tasks;
- reminders;
- calendar events;
- при необходимости recurring items.

Это лучше, чем заставлять LLM самостоятельно делать 3–4 поиска каждый раз.

Например:

> Что у меня завтра?

Agent:

```text
get_schedule(tomorrow)
```

И получает единый структурированный результат.

---

# 16. Tool: поиск

Нужен единый высокоуровневый поиск:

```text
search_everything(
    query,
    start_date?,
    end_date?
)
```

Он может искать:

- tasks;
- reminders;
- calendar;
- notes.

Но результат должен содержать тип сущности и ID:

```json
{
  "type": "task",
  "id": 42,
  "text": "Позвонить Саше",
  "date": "2026-09-11"
}
```

Это намного лучше, чем возвращать только текст.

---

# 17. Tool calling вместо огромного ParsedAction

Удалить концептуальную зависимость агента от:

```text
ParsedAction
```

как главного интерфейса между LLM и приложением.

`ParsedAction` можно временно оставить для legacy flow или постепенно удалить после миграции.

Не делать:

```text
LLM → ParsedAction → if flags → service
```

Делать:

```text
LLM → tool call → tool → result → LLM
```

---

# 18. Agent loop

Нужен ограниченный цикл:

```python
for iteration in range(MAX_TOOL_ITERATIONS):
    response = llm(...)

    if response contains tool_call:
        result = execute_tool(...)
        append_tool_result(...)
        continue

    return final_response
```

Например:

```text
MAX_TOOL_ITERATIONS = 8
```

Защита от бесконечных циклов обязательна.

---

# 19. Tool execution должен быть типизированным

Не использовать:

```python
eval()
exec()
```

Не давать LLM выполнять произвольный Python.

Создать whitelist:

```python
TOOL_REGISTRY = {
    "create_task": create_task_tool,
    "get_tasks": get_tasks_tool,
    "search_tasks": search_tasks_tool,
    ...
}
```

LLM может вызвать только tool из registry.

---

# 20. Права пользователя

Каждый tool получает:

```text
user_id / chat_id
```

из trusted application context.

LLM НЕ должна сама задавать `user_id`.

Например:

```python
await create_task_tool(
    user_id=current_user_id,
    text=...
)
```

а не:

```python
create_task(user_id=llm_generated_id)
```

---

# 21. Подтверждения опасных действий

Добавить policy.

Без подтверждения:

- создание обычной задачи;
- создание reminder;
- просмотр;
- поиск;
- создание обычной заметки;
- создание календарного события, если пользователь явно попросил.

С подтверждением:

- удалить все задачи;
- удалить много задач;
- удалить много reminders;
- массово менять данные;
- очищать раздел заметок;
- потенциально разрушительные действия.

Агент должен понимать разницу между:

> "удали задачу X"

и

> "удали все мои задачи".

---

# 22. Естественный язык

Groot должен нормально понимать:

```text
завтра
послезавтра
через 2 часа
через полчаса
через 3 дня
в пятницу
в следующую пятницу
на следующей неделе
в конце недели
сегодня вечером
после обеда
утром
днём
вечером
```

Но:

## Не выдумывать точное время

Если:

> "завтра вечером"

и нет настройки пользователя для evening time:

спросить:

> Во сколько вечером?

Можно в будущем добавить user preference:

```text
morning_time
afternoon_time
evening_time
```

Но не вводить случайные значения.

---

# 23. Часовой пояс

Сохраняется текущая система:

```text
TIMEZONE=Europe/Moscow
```

Использовать:

```python
get_now()
get_today()
get_tz()
```

Все datetime должны быть timezone-aware.

Не смешивать naive/aware datetime.

---

# 24. Исправить текущую проблему с датами

В текущем `llm.py` есть значительный объём post-processing:

- исправление относительных дат;
- поиск дат regex;
- исправление 12-часового времени;
- очистка задач;
- split multiline tasks;
- auto reminder;
- поиск по ключевым словам.

После внедрения агента:

## Убрать дублирование логики из LLM слоя.

Нормализацию дат сделать отдельным deterministic utility:

```text
app/agent/time_parser.py
```

или использовать хорошо типизированные datetime values, которые LLM уже возвращает через tool schema.

LLM отвечает за смысл.

Deterministic code отвечает за:

- timezone;
- проверку даты;
- проверку времени;
- невозможные значения;
- past datetime;
- DST/UTC conversion.

---

# 25. Мультитаскинг

Это важная возможность.

User:

> Сегодня надо:
> купить корм
> написать Саше
> проверить сервер
> и в 19:00 напомни про сервер

Agent должен создать:

```text
task 1: Купить корм
task 2: Написать Саше
task 3: Проверить сервер
reminder: 19:00 — Проверить сервер
```

Не создавать одну задачу с огромным текстом.

---

# 26. Комбинированные запросы

Поддержать:

> Добавь задачу "позвонить маме" на завтра и напомни в 18:00.

→ create_task + create_reminder

> Запиши встречу с Сашей завтра в 15:00 и напомни за час.

→ create_calendar_event + create_reminder

> Покажи мои планы на завтра и найди, когда я записал стоматолога.

→ get_schedule + search_everything

> Перенеси встречу с Сашей на пятницу и напомни за час.

→ search_calendar_events → update_calendar_event → create/update reminder

---

# 27. Разница между task, reminder и calendar event

Это должно быть явно прописано в agent prompt.

## Task

Дело, которое нужно сделать.

Пример:

> купить корм завтра

## Reminder

Уведомление в конкретное время.

Пример:

> напомни в 18:00 купить корм

## Calendar event

Событие/встреча, которое занимает время.

Пример:

> встреча с Сашей завтра в 18:00

Один пользовательский запрос может создать несколько сущностей.

---

# 28. Голос

Существующий STT не переписывать.

Поток:

```text
voice
 ↓
Whisper
 ↓
recognized text
 ↓
same Agent pipeline
```

Голос не должен иметь отдельную бизнес-логику.

---

# 29. Vision

Vision тоже должен приводить к тому же Agent pipeline.

Например:

Пользователь прислал фото талона:

> [фото]

Agent должен получить извлечённые:

```text
дата
время
место
врач
```

и затем сам решить:

```text
create_calendar_event()
create_reminder()
```

Если на фото просто информация:

> запомни это

→ create_note()

---

# 30. Obsidian

Не удалять существующий WebDAV sync.

Лучше разделить:

```text
core data
    ↓
tasks/notes/etc
    ↓
optional Obsidian sync
```

Agent не должен напрямую работать с WebDAV.

Он вызывает application tool, а тот уже использует:

```python
obsidian_service
```

---

# 31. UI Telegram

Существующие handlers и inline keyboards сохранить.

AI Agent не должен ломать:

- `/today`
- `/goals`
- `/habits`
- `/notes`
- `/settings`
- `/cancel`
- кнопки задач;
- кнопки reminder;
- snooze;
- task edit;
- task move;
- note folders.

Ручной UI должен продолжать работать независимо от Agent.

---

# 32. Что делать с `execute_action_pipeline`

Не удалять сразу.

План миграции:

### Phase 1

Создать Agent и tools.

### Phase 2

Добавить новый route:

```python
handle_text_message()
    ↓
agent.process(...)
```

### Phase 3

Перенести функциональность по tool'ам.

### Phase 4

Удалить старый ParsedAction flow, когда все тесты проходят.

Или оставить compatibility adapter на время.

Не делать big-bang rewrite без необходимости.

---

# 33. Новая рекомендуемая структура

```text
app/
├── agent/
│   ├── __init__.py
│   ├── agent.py
│   ├── tools.py
│   ├── registry.py
│   ├── prompts.py
│   ├── context.py
│   ├── schemas.py
│   └── time_parser.py
│
├── services/
│   ├── calendar.py
│   ├── context.py
│   ├── goals.py
│   ├── llm.py
│   ├── notes.py
│   ├── obsidian.py
│   ├── recurring.py
│   ├── scheduler.py
│   ├── stt.py
│   └── tasks.py
│
└── handlers/
    ├── text.py
    ├── voice.py
    └── media.py
```

Если Antigravity предложит более удачную структуру — можно использовать её, но agent orchestration должен быть отделён от Telegram handlers и database services.

---

# 34. System prompt агента

Prompt должен быть коротким и смысловым.

Пример концепции:

```text
Ты — Groot, личный AI-ассистент пользователя.

Ты не являешься парсером ключевых слов.

Ты понимаешь естественный язык, учитываешь историю текущего диалога и используешь доступные инструменты.

Правила:

1. Не выдумывай факты.
2. Не выдумывай дату/время, если они неизвестны.
3. Используй tools для фактических действий.
4. После tool call анализируй результат.
5. Если нужно несколько действий — выполни несколько tools.
6. Если данных недостаточно — задай уточняющий вопрос.
7. Перед массовыми/опасными удалениями требуй подтверждение.
8. Всегда учитывай timezone пользователя.
9. Не сообщай, что действие выполнено, пока tool действительно не вернул успех.
10. Никогда не создавай данные только потому, что это вероятно.
```

---

# 35. Tool descriptions

Tool descriptions должны быть очень качественными.

Например:

```text
create_reminder

Создаёт одноразовое Telegram-напоминание.

Используй, когда пользователь хочет получить уведомление в определённый момент.

НЕ используй только для создания задачи без уведомления.

Если пользователь сказал "напомни завтра", но не указал время,
не выбирай случайное время — сначала уточни время.
```

Такие описания важнее, чем огромный список boolean fields.

---

# 36. Ответы пользователю

Final response генерирует Agent.

Не нужно заставлять LLM возвращать:

```json
confirmation_text
```

Tools должны возвращать структурированный результат:

```json
{
  "success": true,
  "id": "123",
  "message": "Reminder created"
}
```

А Agent превращает его в:

> 🌴 Готово! Напомню завтра в 15:00 позвонить Саше.

---

# 37. Ошибки

Tools никогда не должны скрывать ошибку.

Пример:

```json
{
  "success": false,
  "error_code": "calendar_not_configured",
  "message": "Google Calendar is not configured"
}
```

Agent:

> Не смог добавить встречу в календарь: интеграция Google Calendar сейчас не настроена. Само напоминание могу создать.

Если пользователь просил два действия и одно успешно, а второе нет — честно сообщить оба результата.

---

# 38. Idempotency

Важно избежать дублей.

Например, если LLM повторно вызывает:

```text
create_reminder()
```

после сетевой ошибки, можно получить два одинаковых reminder.

Добавить по возможности idempotency key:

```text
user_id + operation_id
```

или хотя бы проверку на недавний duplicate.

Особенно для:

- calendar events;
- reminders;
- tasks.

---

# 39. Логирование

Добавить понятное логирование:

```text
agent.request
agent.tool_call
agent.tool_result
agent.final_response
```

Пример:

```text
Agent request: "Перенеси звонок Саше на завтра"
Tool call: search_everything(query="звонок Саше")
Tool result: 1 task found
Tool call: move_task(task_id=42, target_date=...)
Tool result: success
Agent response: "🌴 Готово..."
```

Не логировать API keys, токены и чувствительные данные.

---

# 40. Fallback

Если LLM API недоступен:

- не падать всем handler'ом;
- вернуть понятное сообщение;
- существующие deterministic функции UI продолжить поддерживать.

Например:

> ⚠️ Сейчас AI недоступен. Попробуйте воспользоваться кнопками меню или повторить запрос позже.

---

# 41. Модель

Текущая система использует Groq с несколькими моделями и Gemini fallback.

При рефакторинге не привязывать Agent напрямую к одному конкретному provider.

Желательно создать интерфейс:

```python
class LLMProvider:
    async def run_agent(...)
```

или хотя бы:

```text
AIClient
```

который позволяет менять модель без переписывания tools.

Не ломать текущий fallback.

---

# 42. Не нужно добавлять MCP ради MCP

Для этого проекта MCP не является обязательным.

Внутренние tools обычного Python registry достаточно.

MCP можно рассмотреть позже, если появится необходимость подключать внешние независимые серверы.

Сейчас важнее сделать чистый tool-calling architecture.

---

# 43. Тесты

Обязательно создать tests.

Минимальный набор:

```text
tests/
├── test_agent_tasks.py
├── test_agent_reminders.py
├── test_agent_calendar.py
├── test_agent_context.py
├── test_agent_multiaction.py
└── test_time_parsing.py
```

---

# 44. Acceptance tests

## Test 1

Input:

> Напомни завтра в 15:00 позвонить Саше

Expected:

- one reminder;
- no duplicate task unless current product policy intentionally creates task;
- date correct;
- time 15:00.

---

## Test 2

Input:

> Напомни завтра позвонить Саше

Expected:

Agent asks time.

---

## Test 3

Conversation:

> Напомни завтра позвонить Саше

Bot asks time.

> В 15

Expected:

Reminder created for tomorrow 15:00.

---

## Test 4

Input:

> Завтра стоматолог в 15:00, добавь в календарь и напомни за час

Expected:

- calendar event at 15:00;
- reminder at 14:00;
- exactly two actions.

---

## Test 5

Input:

> Что у меня завтра?

Expected:

- aggregated schedule;
- no creation/modification.

---

## Test 6

Input:

> Найди когда у меня стоматолог

Expected:

- search tasks/reminders/calendar;
- answer with actual result.

---

## Test 7

Input:

> Перенеси стоматолога на пятницу

Expected:

- search first;
- if one result, move it;
- if multiple, ask which.

---

## Test 8

Input:

> Удали все задачи

Expected:

- confirmation before destructive action.

---

## Test 9

Input:

> Запомни, что пароль от тестового сервера хранится в Bitwarden

Expected:

- create note;
- no task.

---

## Test 10

Input:

> Каждый день в 9 утра напоминай пить воду

Expected:

- create recurring task/habit;
- schedule recurring job.

---

## Test 11

Input:

> Сегодня:
> - купить корм
> - проверить сервер
> - написать Саше
> Напомни в 18:00 про сервер

Expected:

- 3 separate tasks;
- 1 reminder.

---

## Test 12

Voice transcript:

> Завтра в три встреча с Сашей, напомни за полчаса

Expected:

same behavior as text.

---

# 45. Что НЕ делать

Не делать:

```text
LLM → огромный ParsedAction
```

как основную архитектуру.

Не делать:

```text
if "напомни" in text
```

как основной NLU.

Не делать:

```text
if "завтра" in text
```

для принятия бизнес-решений.

Не делать:

```text
LLM → SQL
```

LLM не должна писать SQL.

Не делать:

```text
LLM → arbitrary Python
```

Не давать LLM доступ к filesystem shell.

Не удалять старую функциональность без необходимости.

Не менять Telegram UI только ради архитектурной красоты.

Не менять базу данных без миграционной необходимости.

Не удалять существующие данные.

---

# 46. Особое требование по совместимости

После рефакторинга должны продолжить работать:

```text
/start
/today
/goals
/habits
/notes
/settings
/cancel
```

А также:

- задачи;
- заметки;
- цели;
- привычки;
- reminders;
- snooze;
- Google Calendar;
- Obsidian/WebDAV;
- voice;
- image/document input;
- timezone;
- quiet hours;
- automatic task rollover;
- Docker deployment.

---

# 47. Deployment

Не ломать:

```text
Dockerfile
docker-compose.yml
.env
credentials/service_account.json
data/scheduler.db
```

Текущая среда использует:

```text
python:3.11-slim
ffmpeg
tzdata
```

SQLite должен оставаться persistent через:

```text
./data:/app/data
```

credentials:

```text
./credentials:/app/credentials
```

---

# 48. Конфигурация

Не хардкодить:

- API keys;
- Telegram user IDs;
- timezone;
- calendar ID;
- database path;
- WebDAV credentials.

Использовать существующий `settings`.

Если нужны новые настройки:

```text
AGENT_MAX_ITERATIONS
AGENT_MODEL
AGENT_HISTORY_LIMIT
AGENT_CONFIRM_DESTRUCTIVE_ACTIONS
```

добавить в `config.py` и `.env.example`.

---

# 49. Миграция

Сделать миграцию постепенно.

### Шаг 1

Создать tools.

### Шаг 2

Создать Agent.

### Шаг 3

Подключить Agent к text handler.

### Шаг 4

Подключить voice к Agent.

### Шаг 5

Подключить vision к Agent.

### Шаг 6

Перенести context.

### Шаг 7

Добавить tests.

### Шаг 8

Проверить все legacy handlers.

### Шаг 9

Только после этого удалить старую ParsedAction orchestration.

---

# 50. Критически важное UX-требование

Groot должен ощущаться как один ассистент.

Пользователь не должен думать:

> "Какую команду сейчас надо использовать?"

Он должен писать обычным языком:

> "Поставь мне завтра утром задачу проверить сервер."

> "А ещё вечером напомни про неё."

> "Нет, лучше в 20."

> "И добавь это в календарь."

> "Что у меня вообще завтра?"

И Groot должен понимать контекст.

---

# 51. Состояние FSM

Текущие FSM handlers нельзя сломать.

Но там, где возможно, постепенно заменить отдельные состояния типа:

```text
waiting_for_time
waiting_for_date
```

на Agent context.

При этом не обязательно сразу удалять FSM.

Рекомендуемый подход:

- UI-driven flows остаются FSM;
- свободный естественный диалог работает через Agent.

---

# 52. Контекст после tool call

После успешного создания сущности сохранять её как last referenced entity:

```json
{
  "type": "reminder",
  "id": "abc",
  "text": "Позвонить Саше",
  "datetime": "..."
}
```

Тогда:

> Перенеси его на 17:00

можно интерпретировать.

Но если контекст неоднозначен:

> У тебя несколько подходящих объектов. Какой именно перенести?

---

# 53. Структура tool result

Рекомендуемый формат:

```json
{
  "success": true,
  "entity_type": "task",
  "entity_id": 42,
  "data": {
    "text": "Позвонить Саше",
    "date": "2026-09-11"
  }
}
```

Ошибка:

```json
{
  "success": false,
  "error_code": "not_found",
  "message": "Task not found"
}
```

---

# 54. Search result

```json
{
  "success": true,
  "results": [
    {
      "entity_type": "task",
      "entity_id": 42,
      "text": "Позвонить Саше",
      "date": "2026-09-11",
      "time": null
    },
    {
      "entity_type": "reminder",
      "entity_id": "abc123",
      "text": "Позвонить Саше",
      "date": "2026-09-11",
      "time": "15:00"
    }
  ]
}
```

---

# 55. Принцип "сначала прочитай — потом измени"

Для изменения/удаления сущности:

```text
search/get
 ↓
identify exact entity
 ↓
modify/delete
```

Не позволять агенту изменять объект только по приблизительному тексту, если существует неоднозначность.

---

# 56. Производительность

Не вызывать LLM 5 раз для простой операции.

Простой запрос:

> Напомни завтра в 15 позвонить Саше

→ один agent turn + один tool call.

Сложный запрос может иметь несколько tool calls.

Не делать искусственную цепочку.

---

# 57. Стоимость

Не отправлять в модель всю базу пользователя.

Для поиска использовать tools.

LLM получает:

```text
user request
+
small conversation history
+
tool results
```

а не весь SQLite.

---

# 58. Приватность

Никогда не передавать в LLM:

- API keys;
- Telegram bot token;
- WebDAV password;
- Google service account credentials;
- внутренние secrets.

При необходимости чувствительные данные из notes должны передаваться только если это непосредственно нужно для текущей операции.

---

# 59. Финальная архитектура

В итоге:

```text
                     ┌──────────────────┐
                     │ Telegram / Voice │
                     │ Photo / Document │
                     └────────┬─────────┘
                              │
                              ▼
                     ┌──────────────────┐
                     │ Input Processor  │
                     └────────┬─────────┘
                              │
                              ▼
                     ┌──────────────────┐
                     │    AI AGENT      │
                     │                  │
                     │ context          │
                     │ reasoning        │
                     │ tool selection   │
                     └────────┬─────────┘
                              │
              ┌───────────────┼────────────────┐
              │               │                │
              ▼               ▼                ▼
        Tasks Tools      Reminder Tools   Calendar Tools
              │               │                │
              ▼               ▼                ▼
        tasks_service    scheduler_service  calendar_service
              │               │                │
              └───────────────┼────────────────┘
                              │
                              ▼
                     ┌──────────────────┐
                     │ Tool Result      │
                     └────────┬─────────┘
                              │
                              ▼
                         AI AGENT
                              │
                              ▼
                     Natural response
```

---

# 60. Definition of Done

Работа считается выполненной только если:

- [ ] Agent существует отдельно от Telegram handlers.
- [ ] LLM больше не обязана заполнять огромный `ParsedAction`.
- [ ] Есть registry tools.
- [ ] Agent умеет делать несколько tool calls за один запрос.
- [ ] Tool result возвращается обратно агенту.
- [ ] Есть краткосрочный conversational context.
- [ ] Работают follow-up сообщения типа "в 15", "перенеси его", "отмени это".
- [ ] Работают задачи.
- [ ] Работают reminders.
- [ ] Работает calendar.
- [ ] Работают notes.
- [ ] Работают goals.
- [ ] Работают habits/recurring.
- [ ] Работает aggregated schedule.
- [ ] Работает поиск.
- [ ] Voice использует тот же Agent.
- [ ] Vision использует тот же Agent.
- [ ] Сохраняется timezone.
- [ ] Сохраняется SQLite.
- [ ] Сохраняется APScheduler.
- [ ] Сохраняется Obsidian/WebDAV.
- [ ] Сохраняется Google Calendar.
- [ ] Сохраняются Telegram UI handlers.
- [ ] Добавлены tests.
- [ ] Нет destructive actions без подтверждения.
- [ ] Нет arbitrary code execution.
- [ ] Нет API secrets в prompts/logs.
- [ ] Docker deployment продолжает работать.
- [ ] Existing data не теряется.
- [ ] Все acceptance tests проходят.

---

# 61. Инструкция Antigravity перед началом работы

НЕ НАЧИНАЙ СРАЗУ ПЕРЕПИСЫВАТЬ КОД.

Сначала:

1. Просканируй весь репозиторий.
2. Найди все места, где используется `ParsedAction`.
3. Найди все места, где вызывается `llm_service`.
4. Найди все методы каждого service, которые можно обернуть в tools.
5. Найди все Telegram handlers и FSM flows.
6. Проверь текущие database schemas.
7. Проверь APScheduler jobs.
8. Проверь Google Calendar integration.
9. Проверь Obsidian/WebDAV integration.
10. Проверь voice/media pipeline.
11. Построй dependency map.
12. Составь короткий план миграции.
13. Только после этого начинай изменения.

Перед удалением любого существующего кода убедись, что его функциональность уже покрыта новой архитектурой.

---

# 62. Главное требование

Не пытайся сделать "ещё более умный JSON parser".

Нужно изменить саму модель взаимодействия:

```text
СТАРОЕ:

User
 ↓
LLM
 ↓
огромный JSON
 ↓
if/elif
 ↓
service


НОВОЕ:

User
 ↓
LLM Agent
 ↓
Tool
 ↓
real application action
 ↓
Tool result
 ↓
LLM Agent
 ↓
next tool / question / final answer
```

Именно это является основной целью задачи.

Groot должен стать не "нейросетью, которая угадывает, в какой JSON key положить слово", а агентом, который понимает намерение пользователя, умеет пользоваться функциями бота, видеть результат своих действий и продолжать диалог с учётом контекста.
