# Friday's Bot — Личный Мультимодальный Telegram-Ассистент 🤖

Production-ready Telegram-бот на базе **aiogram 3.x**, **Groq API** (Whisper STT & Llama Vision/LLM), **WebDAV** (Obsidian), **Google Calendar** и **APScheduler**.

---

## 🛠 Стек технологий:
- **Telegram:** `aiogram 3.x`
- **STT (Голос в текст):** Groq API (`whisper-large-v3-turbo`)
- **LLM & Multimodal Vision:** Groq API (`llama-3.3-70b-versatile` / `llama-3.2-11b-vision-preview`) с поддержкой Gemini Flash
- **Obsidian Sync:** `webdavclient3` (Mail.ru Cloud WebDAV)
- **Планировщик напоминаний:** `APScheduler` (`AsyncIOScheduler`) + `aiosqlite`
- **Календарь:** Google Calendar API v3 (`google-api-python-client`)
- **Конфигурация:** `pydantic-settings`
- **Деплой:** Docker, Docker Compose

---

## 🚀 Пошаговая инструкция по развёртыванию на VPS за 3 шага

### Шаг 1. Скопируйте репозиторий на ваш VPS
```bash
git clone https://github.com/your-user/fridays_bot.git /opt/fridays_bot
cd /opt/fridays_bot
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
docker compose logs -f friday_bot
```

---

## 📁 Структура проекта:
```
Friday's_bot/
├── .env.example
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── README.md
├── To-Do template.md
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
    │   ├── obsidian.py
    │   ├── calendar.py
    │   └── scheduler.py
    └── handlers/
        ├── common.py
        ├── text.py
        ├── voice.py
        └── media.py
```
