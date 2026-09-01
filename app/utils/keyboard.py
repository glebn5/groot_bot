from aiogram.types import ReplyKeyboardMarkup, KeyboardButton


def get_main_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="📅 Планы на сегодня"),
                KeyboardButton(text="🎯 Цели на месяц")
            ],
            [
                KeyboardButton(text="🔁 Привычки"),
                KeyboardButton(text="📝 Мои заметки")
            ],
            [
                KeyboardButton(text="⚙️ Настройки")
            ]
        ],
        resize_keyboard=True,
        is_persistent=True
    )
