import logging
from datetime import datetime, date
from typing import Optional
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from app.services.goals import goals_service
from app.utils.timezone import get_today

logger = logging.getLogger(__name__)
router = Router(name="goals")


class GoalAddForm(StatesGroup):
    target_month = State()
    waiting_for_text = State()


MONTH_NAMES_RU = {
    1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
    5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
    9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь"
}


def format_month_name(month_str: str) -> str:
    """Converts 'YYYY-MM' to 'Месяц YYYY' in Russian."""
    try:
        dt = datetime.strptime(month_str, "%Y-%m")
        name = MONTH_NAMES_RU.get(dt.month, "")
        return f"{name} {dt.year}"
    except Exception:
        return month_str


def get_prev_next_months(month_str: str):
    """Returns (prev_month_str, next_month_str) given 'YYYY-MM'."""
    dt = datetime.strptime(month_str, "%Y-%m")
    # Prev month
    if dt.month == 1:
        prev_dt = datetime(dt.year - 1, 12, 1)
    else:
        prev_dt = datetime(dt.year, dt.month - 1, 1)

    # Next month
    if dt.month == 12:
        next_dt = datetime(dt.year + 1, 1, 1)
    else:
        next_dt = datetime(dt.year, dt.month + 1, 1)

    return prev_dt.strftime("%Y-%m"), next_dt.strftime("%Y-%m")


async def render_goals_view(user_id: int, target_month: Optional[str] = None):
    if not target_month:
        target_month = get_today().strftime("%Y-%m")

    goals = await goals_service.get_goals(user_id, target_month)
    month_name = format_month_name(target_month)
    prev_m, next_m = get_prev_next_months(target_month)

    completed_count = sum(1 for g in goals if g["is_completed"])
    total_count = len(goals)

    lines = [
        f"🎯 **Цели на {month_name}:**",
    ]

    if total_count > 0:
        lines.append(f"📊 _Прогресс: {completed_count}/{total_count} выполнено_\n")
    else:
        lines.append("\nУ тебя пока нет целей на этот месяц! Нажми «➕ Добавить цель» или просто напиши мне: *«Поставь цель на этот месяц...»* ✨\n")

    buttons = []

    for idx, g in enumerate(goals, 1):
        g_id = g["id"]
        is_comp = bool(g["is_completed"])
        icon = "✅" if is_comp else "⬜"
        text = g["goal_text"]

        if is_comp:
            lines.append(f"~{idx}. {text}~")
        else:
            lines.append(f"**{idx}. {text}**")

        buttons.append([
            InlineKeyboardButton(text=f"{icon} #{idx}", callback_data=f"toggle_g:{g_id}:{target_month}"),
            InlineKeyboardButton(text=f"🗑 Удалить", callback_data=f"del_g:{g_id}:{target_month}")
        ])

    # Add goal button
    buttons.append([
        InlineKeyboardButton(text="➕ Добавить цель", callback_data=f"add_g_prompt:{target_month}")
    ])

    # Navigation buttons
    buttons.append([
        InlineKeyboardButton(text=f"◀ {format_month_name(prev_m)}", callback_data=f"g_month:{prev_m}"),
        InlineKeyboardButton(text=f"{format_month_name(next_m)} ▶", callback_data=f"g_month:{next_m}")
    ])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    return "\n".join(lines), keyboard


@router.message(Command("goals"))
@router.message(F.text.in_({"🎯 Цели на месяц", "Цели на месяц"}))
async def cmd_goals(message: Message):
    text, reply_markup = await render_goals_view(message.from_user.id)
    await message.answer(text, reply_markup=reply_markup, parse_mode="Markdown")


@router.callback_query(F.data.startswith("g_month:"))
async def process_goals_month(callback: CallbackQuery):
    target_month = callback.data.split(":", 1)[1]
    text, reply_markup = await render_goals_view(callback.from_user.id, target_month)
    await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode="Markdown")


@router.callback_query(F.data.startswith("toggle_g:"))
async def process_toggle_goal(callback: CallbackQuery):
    parts = callback.data.split(":")
    g_id = int(parts[1])
    target_month = parts[2]

    res = await goals_service.toggle_goal(g_id, callback.from_user.id)
    if res is not None:
        status_str = "выполнена ✅" if res else "переведена в активные ⬜"
        await callback.answer(f"Цель {status_str}")
    else:
        await callback.answer("Цель не найдена.")

    text, reply_markup = await render_goals_view(callback.from_user.id, target_month)
    await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode="Markdown")


@router.callback_query(F.data.startswith("del_g:"))
async def process_delete_goal(callback: CallbackQuery):
    parts = callback.data.split(":")
    g_id = int(parts[1])
    target_month = parts[2]

    await goals_service.delete_goal(g_id, callback.from_user.id)
    await callback.answer("Цель удалена.")

    text, reply_markup = await render_goals_view(callback.from_user.id, target_month)
    await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode="Markdown")


@router.callback_query(F.data.startswith("add_g_prompt:"))
async def process_add_goal_prompt(callback: CallbackQuery, state: FSMContext):
    target_month = callback.data.split(":", 1)[1]
    await state.update_data(target_month=target_month)
    await state.set_state(GoalAddForm.waiting_for_text)
    
    month_name = format_month_name(target_month)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"cancel_g_add:{target_month}")]
    ])
    await callback.message.answer(
        f"🎯 **Добавление цели на {month_name}:**\n\n"
        f"Напишите текст вашей цели одним сообщением.\n"
        f"Для отмены нажмите кнопку ниже или отправьте /cancel.",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cancel_g_add:"))
async def process_cancel_g_add_callback(callback: CallbackQuery, state: FSMContext):
    target_month = callback.data.split(":", 1)[1]
    await state.clear()
    await callback.answer("Добавление цели отменено.")
    text, reply_markup = await render_goals_view(callback.from_user.id, target_month)
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    except Exception:
        await callback.message.answer(text, reply_markup=reply_markup, parse_mode="Markdown")


@router.message(Command("cancel"), GoalAddForm.waiting_for_text)
@router.message(F.text.in_({"cancel", "/cancel", "отмена", "Отмена", "❌ Отмена", "🔙 Отмена"}), GoalAddForm.waiting_for_text)
async def process_cancel_add_goal(message: Message, state: FSMContext):
    data = await state.get_data()
    target_month = data.get("target_month") or get_today().strftime("%Y-%m")
    await state.clear()
    text, reply_markup = await render_goals_view(message.from_user.id, target_month)
    await message.answer("❌ Добавление цели отменено.", reply_markup=reply_markup, parse_mode="Markdown")


@router.message(GoalAddForm.waiting_for_text)
async def process_save_goal_text(message: Message, state: FSMContext):
    data = await state.get_data()
    target_month = data.get("target_month") or get_today().strftime("%Y-%m")
    goal_text = message.text.strip() if message.text else ""

    if goal_text.lower() in ["/cancel", "cancel", "отмена", "❌ отмена", "🔙 отмена"]:
        await state.clear()
        text, reply_markup = await render_goals_view(message.from_user.id, target_month)
        await message.answer("❌ Добавление цели отменено.", reply_markup=reply_markup, parse_mode="Markdown")
        return

    await state.clear()

    if goal_text:
        await goals_service.add_goal(message.from_user.id, goal_text, target_month)
        await message.answer(f"🎯 **Цель добавлена!**\n_«{goal_text}»_")

    text, reply_markup = await render_goals_view(message.from_user.id, target_month)
    await message.answer(text, reply_markup=reply_markup, parse_mode="Markdown")
