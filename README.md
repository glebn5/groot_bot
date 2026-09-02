# Groot Bot — Личный Мультимодальный Telegram-Ассистент 🌴

Production-ready Telegram-бот на базе **aiogram 3.x**, **Groq API** (Whisper STT & Llama 3.3 / Llama Vision), **Obsidian WebDAV Sync**, **Google Calendar API** и **APScheduler**.

---

## 🌟 Основные возможности:

### 1. 📅 Планировщик и Задачи (`/today`, `📅 Планы на сегодня`)
- Управление задачами на любой день (сегодня, вчера, завтра, произвольная дата).
- Установка точного времени задач (`15:00 - МВД`) и авто-напоминания (в 08:00, 12:00, 15:00, 19:00 или кастомные).
- Умная сортировка: **сначала невыполненные задачи (`▫️`)**, затем **выполненные (`✅`)** с сохранением порядка создания.
- Интерактивный UI с пагинацией, подробным просмотром, изменением текста (с кнопкой копирования в 1 клик), переносом даты и времени.

### 2. 📝 Заметки и Разделы/Папки (`/notes`, `📝 Мои заметки`)
- Система **разделов (папок)** заметок (*Работа*, *Личное*, *Идеи* и категория *Без раздела*).
- Вертикальная раскладка функциональных кнопок для удобства управления с мобильных устройств.
- Автоматический выбор раздела при создании заметки через текст или голос.
- Перемещение заметок между разделами.
- Безопасное удаление раздела с выбором: каскадное удаление или перенос заметок в category «Без раздела».

### 3. 🎯 Цели на месяц (`/goals`, `🎯 Цели на месяц`)
- Помесячный чек-лист целей (`ГГГГ-ММ`) с прогрессом выполнения.
- Автоматические еженедельные напоминания в выбранные дни и время.

### 4. 🔁 Привычки и Повторяющиеся задачи (`/habits`, `🔁 Привычки`)
- Настройка регулярных ритуалов (ежедневно, по дням недели, с интервалом в N дней/часов/минут).
- Уведомления с быстрыми кнопками: `✅ Сделано`, `⏳ Напомнить 15м`, `❌ Пропустить`.

### 5. ⏰ Умные Напоминания (`APScheduler` + SQLite)
- Разовые и гибкие напоминания на любую дату и время.
- Быстрое откладывание (на 5 мин, N мин или фиксированное время).
- Редактирование времени и текста имеющихся напоминаний.

### 6. 🎙 Голос, Изображения и ИИ (Groq API & Gemini)
- Высокоточное распознавание речи через **Groq Whisper Large v3 Turbo**.
- Извлечение намерений (NLU) через **Llama 3.3 70B Versatile** / **Gemini Flash**.
- Анализ фотографий и документов через **Llama 3.2 Vision**.

### 7. 🔄 Синхронизация (Obsidian & Google Calendar)
- Двусторонняя выгрузка ежедневников в Obsidian через **WebDAV** (Mail.ru Cloud, Yandex, Nextcloud).
- Интеграция с **Google Calendar API** для создания и просмотра событий.

---

## 🛠 Стек технологий:
- **Telegram Framework:** `aiogram 3.x`
- **STT (Speech-to-Text):** Groq API (`whisper-large-v3-turbo`)
- **LLM & Multimodal Vision:** Groq API (`llama-3.3-70b-versatile` / `llama-3.2-11b-vision-preview`) & Google Gemini
- **Obsidian Sync:** `webdavclient3`
- **Scheduler & Storage:** `APScheduler` (`AsyncIOScheduler`) + `sqlite3`
- **Calendar API:** Google Calendar API v3 (`google-api-python-client`)
- **Configuration:** `pydantic-settings`
- **Deployment:** Docker, Docker Compose, systemd

---

## 🚀 Пошаговая инструкция по развёртыванию на VPS за 3 шага

### Шаг 1. Скопируйте репозиторий на ваш VPS
```bash
git clone https://github.com/your-user/groot_bot.git /opt/groot_bot
cd /opt/groot_bot
```

### Шаг 2. Создайте файл окружения `.env` и ключи Google
```bash
cp .env.example .env
nano .env
```
Заполните обязательные переменные в `.env`:
- `BOT_TOKEN` (полученный у @BotFather)
- `ALLOWED_TELEGRAM_USER_IDS` (ваш Telegram ID)
- `GROQ_API_KEY` (ключ с console.groq.com)
- `WEBDAV_LOGIN` и `WEBDAV_PASSWORD` (пароль приложения Mail.ru Cloud WebDAV)

Поместите файл Service Account от Google в папку `credentials`:
```bash
mkdir -p credentials data
# Загрузите ваш ключ service_account.json в credentials/service_account.json
```

### Шаг 3. Запустите бота через Docker Compose
```bash
docker compose up -d --build
```

Проверить статус работы и логи бота:
```bash
docker compose logs -f groot_bot
```

---

## 📁 Структура проекта:
```
groot_bot/
├── .env.example
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── README.md
├── credentials/
│   └── service_account.json
├── data/
│   └── scheduler.db
└── app/
    ├── __init__.py
    ├── main.py
    ├── config.py
    ├── middleware/
    │   └── auth.py
    ├── models/
    │   └── schemas.py
    ├── services/
    │   ├── stt.py
    │   ├── llm.py
    │   ├── notes.py
    │   ├── tasks.py
    │   ├── goals.py
    │   ├── habits.py
    │   ├── recurring.py
    │   ├── obsidian.py
    │   ├── calendar.py
    │   └── scheduler.py
    └── handlers/
        ├── common.py
        ├── text.py
        ├── notes.py
        ├── goals.py
        ├── habits.py
        ├── settings.py
        ├── voice.py
        └── media.py
```
